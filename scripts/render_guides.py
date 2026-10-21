"""Exact categorical and numeric guides from opaque source geometry.

The Blender BVH samples the center of each global atlas pixel once. Source
feature IDs are not antialiased, shaded, inferred from image color or invented.
Authoritative depth and normals use little-endian float32 binary arrays. PNGs
are portable previews, except feature-id and semantic PNGs which encode exact
categorical values and must never be interpolated.
"""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path

from atlas import validate_bounds
from fetch_sample import unpack_b3dm
from geo import local_point
from gltf_compat import remove_external_rtc
from render_context import CONTEXT_SEMANTICS, SEMANTIC_IDS

MAX_FEATURE_ID = 2**24 - 1


def encode_feature_id(identifier: int) -> tuple[int, int, int]:
    if type(identifier) is not int or not 0 <= identifier <= MAX_FEATURE_ID:
        raise ValueError("Feature ID must fit an unsigned 24-bit integer")
    return identifier >> 16, (identifier >> 8) & 255, identifier & 255


def decode_feature_id(rgb) -> int:
    if len(rgb) != 3 or any(
        type(value) is not int or not 0 <= value <= 255 for value in rgb
    ):
        raise ValueError("Feature ID requires exactly three bytes")
    return (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]


def triangle_feature_id(batch_values, batch_feature_ids):
    if len(batch_values) != 3 or any(type(value) is not int for value in batch_values):
        raise ValueError("Triangle batch IDs must be three integers")
    if len(set(batch_values)) != 1:
        raise ValueError("A triangle crosses source feature IDs")
    batch_id = batch_values[0]
    if not 0 <= batch_id < len(batch_feature_ids):
        raise ValueError("Triangle batch ID is outside its source feature table")
    return batch_feature_ids[batch_id]


def feature_registry(tables, tile_semantics=None):
    """Create one release-wide registry before planning or rendering any chunks.

    Numeric IDs are release-local. The gml_id is the durable identity, retained
    unchanged in the sidecar when source selection expands in a later release.
    Semantic kinds keep unrelated CityGML feature classes distinct.
    """
    tile_semantics = tile_semantics or {key: "building" for key in tables}
    features = {}
    for tile_hash, table in tables.items():
        kind = tile_semantics[tile_hash]
        if kind not in SEMANTIC_IDS or kind in ("background", "ground"):
            raise ValueError("Unknown source feature semantic kind")
        for record in table:
            identity = record.get("gml_id")
            if not isinstance(identity, str) or not identity:
                raise ValueError("Source feature record lacks a genuine gml_id")
            key = (kind, identity)
            if key in features and features[key] != record:
                raise ValueError("Conflicting metadata for the same source gml_id")
            features[key] = record
    records = [{"id": 0, "kind": "background"}, {"id": 1, "kind": "ground"}]
    lookup = {}
    for key in sorted(features):
        number = len(records)
        lookup[key] = number
        records.append({**features[key], "id": number, "kind": key[0]})
    unresolved_tiles = {}
    for tile_hash in sorted(tables):
        number = len(records)
        unresolved_tiles[tile_hash] = number
        records.append(
            {
                "id": number,
                "kind": "unresolved-source-tile",
                "semantic_kind": tile_semantics[tile_hash],
                "source_tile_sha256": tile_hash,
            }
        )
    if len(records) - 1 > MAX_FEATURE_ID:
        raise ValueError("Feature registry exceeds 24-bit PNG capacity")
    return {
        "schema_version": 1,
        "identity_scope": "Numeric IDs are release-local; semantic kind and gml_id retain the source identity across releases",
        "background_id": 0,
        "ground_id": 1,
        "records": records,
        "tile_batch_feature_ids": {
            key: [lookup[(tile_semantics[key], item["gml_id"])] for item in value]
            for key, value in tables.items()
        },
        "unresolved_tile_feature_ids": unresolved_tiles,
    }


def source_registry(manifest_path: Path, *, context_path: Path | None = None):
    """Match original b3dm tables to exact imported GLBs, without network."""
    tables, hashes, semantics = {}, {}, {}
    sources = [(manifest_path, "building")]
    if context_path is not None:
        sources.append((context_path, "context"))
    for path, source_kind in sources:
        manifest = json.loads(path.read_text())
        for number, tile in enumerate(manifest["tiles"]):
            kind = (
                "building"
                if source_kind == "building"
                else CONTEXT_SEMANTICS[tile["context_type"]]
            )
            glb = (path.parent / tile["glb_path"]).read_bytes()
            tile_hash = hashlib.sha256(remove_external_rtc(glb)).hexdigest()
            records = []
            source_path = path.parent / tile["path"]
            if source_path.suffix.lower() == ".b3dm":
                payload = source_path.read_bytes()
                digest = hashlib.sha256(payload).hexdigest()
                if digest != tile["sha256"]:
                    raise ValueError("Original b3dm checksum mismatch")
                embedded, feature, batch = unpack_b3dm(payload)
                if embedded != glb:
                    raise ValueError(
                        "Original b3dm GLB does not match the imported GLB"
                    )
                identifiers = batch.get("gml_id")
                if identifiers is not None:
                    if not isinstance(identifiers, list) or len(
                        identifiers
                    ) != feature.get("BATCH_LENGTH"):
                        raise ValueError(
                            "Source gml_id count does not match BATCH_LENGTH"
                        )
                    official_ids = batch.get(
                        "uro:BuildingIDAttribute_uro:buildingID",
                        [None] * len(identifiers),
                    )
                    if len(official_ids) != len(identifiers):
                        raise ValueError(
                            "Source building-ID column has the wrong length"
                        )
                    records = [
                        {"gml_id": identity, "source_building_id": official_ids[index]}
                        if kind == "building"
                        else {
                            "gml_id": identity,
                            "source_item_id": tile["source_item_id"],
                        }
                        for index, identity in enumerate(identifiers)
                    ]
                hashes[f"{source_kind}-b3dm-{number}"] = digest
            if tile_hash in tables and (
                tables[tile_hash] != records or semantics[tile_hash] != kind
            ):
                raise ValueError(
                    "One GLB content hash has conflicting source feature tables"
                )
            tables[tile_hash] = records
            semantics[tile_hash] = kind
    return feature_registry(tables, semantics), hashes


def canonical_depth_range(config):
    origin = tuple(
        a + b
        for a, b in zip(config.camera_target_enu, config.camera_offset_enu, strict=True)
    )
    forward = config.basis["forward"]
    # The full configured source area, not the current job's pixel rectangle,
    # controls preview scaling. Subset jobs therefore retain identical bytes.
    west, south, east, north = config.source_bounds_wsen
    corners = [
        local_point(lon, lat, 0, *config.origin_lonlat)
        for lon in (west, east)
        for lat in (south, north)
    ]
    buffer = config.shadow_buffer_m
    xs = [
        min(point[0] for point in corners) - buffer,
        max(point[0] for point in corners) + buffer,
    ]
    ys = [
        min(point[1] for point in corners) - buffer,
        max(point[1] for point in corners) + buffer,
    ]
    depths = []
    for x in xs:
        for y in ys:
            for height in config.vertical_range_enu_m:
                point = (x, y, height)
                depths.append(
                    sum((point[i] - origin[i]) * forward[i] for i in range(3))
                )
    return min(depths) - 1.0, max(depths) + 1.0


def pixel_ray(config, pixel, origin_depth_m):
    """Global pixel-center ray, identical regardless of the current chunk."""
    basis = config.basis
    dx = (pixel[0] - config.pixel_origin[0]) / config.pixels_per_metre
    dy = -(pixel[1] - config.pixel_origin[1]) / config.pixels_per_metre
    origin = tuple(
        config.camera_target_enu[i]
        + config.camera_offset_enu[i]
        + basis["right"][i] * dx
        + basis["up"][i] * dy
        + basis["forward"][i] * origin_depth_m
        for i in range(3)
    )
    return origin, basis["forward"]


def _png_chunk(kind, payload):
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload))
    )


def write_png(path: Path, size, pixels: bytes, *, channels=3, bits=8):
    """Small dependency-free PNG writer for Blender's bundled Python.

    Numeric arrays are already encoded before writing; no color transform,
    metadata, alpha blending or resampling may alter the categorical bytes.
    """
    width, height = size
    if type(width) is not int or type(height) is not int or width < 1 or height < 1:
        raise ValueError("PNG dimensions must be positive integers")
    if (channels, bits) not in ((1, 8), (1, 16), (3, 8)):
        raise ValueError("Unsupported guide PNG encoding")
    stride = width * channels * (bits // 8)
    if len(pixels) != height * stride:
        raise ValueError("Guide PNG byte length does not match its dimensions")
    rows = b"".join(
        b"\0" + pixels[row * stride : (row + 1) * stride] for row in range(height)
    )
    header = struct.pack(
        ">IIBBBBB", width, height, bits, 0 if channels == 1 else 2, 0, 0, 0
    )
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(rows))
        + _png_chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def build_guide_scene(objects, config, registry):
    """Build a world-space BVH and validated source identity for every triangle."""
    import numpy as np
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    vertices, triangles, feature_ids, semantic_ids = [], [], [], []
    resolved = {
        kind: 0 for kind in SEMANTIC_IDS if kind not in ("background", "ground")
    }
    unresolved = dict(resolved)
    for obj in objects:
        if obj.type != "MESH":
            continue
        kind = obj.get("atlas_semantic")
        if kind not in SEMANTIC_IDS or kind == "background":
            raise ValueError(
                "Every guide mesh must explicitly identify its source semantic kind"
            )
        offset = len(vertices)
        vertices.extend(
            tuple(obj.matrix_world @ vertex.co) for vertex in obj.data.vertices
        )
        obj.data.calc_loop_triangles()
        tile_hash = obj.get("source_tile_content_sha256")
        batch_attribute = obj.data.attributes.get("_BATCHID")
        batch_ids = None
        batch_feature_ids = registry["tile_batch_feature_ids"].get(tile_hash, [])
        if kind != "ground" and batch_attribute is not None and batch_feature_ids:
            if batch_attribute.domain != "POINT" or batch_attribute.data_type != "INT":
                raise ValueError("Source _BATCHID must be a POINT integer attribute")
            batch_ids = np.empty(len(batch_attribute.data), dtype=np.int32)
            batch_attribute.data.foreach_get("value", batch_ids)
            if len(batch_ids) != len(obj.data.vertices):
                raise ValueError("Source _BATCHID count does not match mesh vertices")
            resolved[kind] += 1
        elif kind != "ground" and "atlas_fixture_feature_id" not in obj:
            unresolved[kind] += 1
        for triangle in obj.data.loop_triangles:
            vertex_ids = tuple(triangle.vertices)
            triangles.append(tuple(offset + vertex for vertex in vertex_ids))
            semantic_ids.append(SEMANTIC_IDS[kind])
            if kind == "ground":
                feature_ids.append(1)
            elif "atlas_fixture_feature_id" in obj:
                feature_ids.append(int(obj["atlas_fixture_feature_id"]))
            elif batch_ids is not None:
                feature_ids.append(
                    triangle_feature_id(
                        [int(batch_ids[vertex]) for vertex in vertex_ids],
                        batch_feature_ids,
                    )
                )
            else:
                if tile_hash not in registry["unresolved_tile_feature_ids"]:
                    raise ValueError(
                        "Unresolved guide object has no genuine source tile identity"
                    )
                feature_ids.append(registry["unresolved_tile_feature_ids"][tile_hash])
    if not triangles:
        raise ValueError("No geometry for guide rays")
    tree = BVHTree.FromPolygons(vertices, triangles, all_triangles=True, epsilon=0.0)
    near, far = canonical_depth_range(config)
    origin_depth = min(-1.0, near - 100)
    return {
        "tree": tree,
        "feature_ids": feature_ids,
        "semantic_ids": semantic_ids,
        "canonical_origin": Vector(
            tuple(
                a + b
                for a, b in zip(
                    config.camera_target_enu, config.camera_offset_enu, strict=True
                )
            )
        ),
        "forward": Vector(config.basis["forward"]),
        "origin_depth_m": origin_depth,
        "ray_length_m": far - origin_depth + 100,
        "depth_range_m": [near, far],
        "resolved_building_meshes": resolved["building"],
        "unresolved_building_meshes": unresolved["building"],
        "resolved_meshes_by_semantic": resolved,
        "unresolved_meshes_by_semantic": unresolved,
        "triangle_count": len(triangles),
    }


def render_guides(config, plan, guide_scene, output: Path, *, max_pixels):
    """Sample core pixels, without shading, filtering or image-border clipping."""
    import numpy as np
    from mathutils import Vector

    left, top, right, bottom = validate_bounds(plan["bounds_px"])
    width, height = right - left, bottom - top
    if width * height > max_pixels:
        raise ValueError("Guide frame exceeds explicit ray/pixel budget")
    features = np.zeros((height, width), dtype=np.uint32)
    semantics = np.zeros((height, width), dtype=np.uint8)
    depth = np.zeros((height, width), dtype="<f4")
    normals = np.zeros((height, width, 3), dtype="<f4")
    for row in range(height):
        for column in range(width):
            origin, direction = pixel_ray(
                config,
                (left + column + 0.5, top + row + 0.5),
                guide_scene["origin_depth_m"],
            )
            point, normal, triangle, _ = guide_scene["tree"].ray_cast(
                Vector(origin), Vector(direction), guide_scene["ray_length_m"]
            )
            if triangle is None:
                continue
            features[row, column] = guide_scene["feature_ids"][triangle]
            semantics[row, column] = guide_scene["semantic_ids"][triangle]
            depth[row, column] = (point - guide_scene["canonical_origin"]).dot(
                guide_scene["forward"]
            )
            normals[row, column] = normal
    feature_rgb = np.stack(
        ((features >> 16) & 255, (features >> 8) & 255, features & 255), axis=-1
    ).astype(np.uint8)
    normal_rgb = np.rint((normals + 1) * 127.5).clip(0, 255).astype(np.uint8)
    normal_rgb[semantics == 0] = 0
    near, far = guide_scene["depth_range_m"]
    depth_preview = (
        (1 + np.rint((depth - near) / (far - near) * 65534))
        .clip(1, 65535)
        .astype(">u2")
    )
    depth_preview[semantics == 0] = 0
    assets = {}

    def png(name, array, channels=1, bits=8):
        relative = Path("guides") / name / f"{plan['id']}.png"
        path = output / relative
        write_png(path, (width, height), array.tobytes(), channels=channels, bits=bits)
        assets[name] = {
            "path": relative.as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "width": width,
            "height": height,
            "bytes": path.stat().st_size,
        }

    def binary(name, array):
        relative = Path("guides") / name / f"{plan['id']}.f32"
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = array.astype("<f4", copy=False).tobytes()
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(payload)
        temporary.replace(path)
        assets[name] = {
            "path": relative.as_posix(),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "width": width,
            "height": height,
            "components": 3 if array.ndim == 3 else 1,
            "dtype": "float32-little-endian",
            "bytes": len(payload),
        }

    png("feature-id", feature_rgb, channels=3)
    png("semantic", semantics)
    png("silhouette", (semantics == 2).astype(np.uint8) * 255)
    png("normal-preview", normal_rgb, channels=3)
    png("depth-preview", depth_preview, bits=16)
    binary("depth", depth)
    binary("normal", normals)
    plan["guides"] = assets
    return assets


def guide_metadata(guide_scene, registry, registry_path, registry_sha256):
    return {
        "schema_version": 1,
        "sampling": "One opaque-geometry BVH ray at each global pixel center; no antialiasing or texture transparency",
        "feature_id": {
            "png": "RGB24 big-endian integer: R*65536 + G*256 + B",
            "background": 0,
            "ground": 1,
            "registry": registry_path,
            "registry_sha256": registry_sha256,
        },
        "semantic": {
            "png": "8-bit grayscale category",
            "categories": {str(number): kind for kind, number in SEMANTIC_IDS.items()},
        },
        "silhouette": "255 for building pixels, 0 for every other category",
        "depth": {
            "format": "Row-major little-endian float32 metres",
            "definition": "dot(hit - (atlas.camera_target_enu + atlas.camera_offset_enu), camera_forward_enu)",
            "background_value": 0,
            "background_mask": "semantic == 0",
            "preview_range_m": guide_scene["depth_range_m"],
            "preview_encoding": "uint16 1..65535 over preview range; values outside range clip; 0 background; authoritative values are in .f32",
            "preview_range_basis": "Full source_bounds_wsen plus shadow_buffer_m and vertical_range_enu_m, independent of the current chunk bounds",
        },
        "normal": {
            "format": "Row-major little-endian float32 ENU xyz triples",
            "definition": "World-space geometric triangle normal, not the interpolated material/shading normal",
            "background_value": [0, 0, 0],
            "preview_encoding": "round((normal + 1) * 127.5), black background; authoritative values are in .f32",
        },
        "resolved_building_meshes": guide_scene["resolved_building_meshes"],
        "unresolved_building_meshes": guide_scene["unresolved_building_meshes"],
        "source_building_registry_count": sum(
            item["kind"] == "building" for item in registry["records"]
        ),
        "source_feature_counts": {
            kind: sum(item["kind"] == kind for item in registry["records"])
            for kind in SEMANTIC_IDS
            if kind not in ("background", "ground")
        },
        "resolved_meshes_by_semantic": guide_scene["resolved_meshes_by_semantic"],
        "unresolved_meshes_by_semantic": guide_scene["unresolved_meshes_by_semantic"],
        "triangle_count": guide_scene["triangle_count"],
    }

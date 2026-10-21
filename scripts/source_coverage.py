"""Audit and acquire bounded source tiles for an isometric view and its shadows.

Selection completeness is an auditable metadata statement, not a claim that
all real-world buildings, ground surfaces or vegetation were surveyed.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import itertools
import json
import math
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, build_opener

from atlas import load_config, parse_config, source_footprint_enu, world_to_pixel
from fetch_context import NoRedirect, context_tile, validate_tileset, validate_url
from fetch_sample import select_leaf_tiles
from geo import local_point, local_to_geodetic

MAX_BYTES = 500_000_000
MAX_TILES = 1024
MARKER = ".coverage-source"


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    )


def fingerprint(value):
    return digest(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    )


class CoverageCache:
    """Checksummed URL cache with separate hard source and actual-network budgets."""

    def __init__(self, root, max_bytes=MAX_BYTES, *, opener=None):
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BYTES:
            raise ValueError("Coverage byte budget must be in [1,500000000]")
        self.root, self.max_bytes = Path(root), max_bytes
        self.opener = opener if opener is not None else build_opener(NoRedirect()).open
        self.network_bytes, self.source_bytes, self.requests = 0, 0, 0
        self.seen = set()

    def fetch(self, url):
        validate_url(url)
        key = digest(url.encode())
        raw_path, meta_path = self.root / (key + ".bin"), self.root / (key + ".json")
        if raw_path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text())
            if meta.get("url") != url or meta.get("bytes") != raw_path.stat().st_size:
                raise ValueError("Coverage cache metadata mismatch")
            size = raw_path.stat().st_size
            if key not in self.seen and self.source_bytes + size > self.max_bytes:
                raise ValueError("Cached source exceeds coverage byte budget")
            blob = raw_path.read_bytes()
            if meta.get("sha256") != digest(blob):
                raise ValueError("Coverage cache checksum mismatch")
        else:
            self.root.mkdir(parents=True, exist_ok=True)
            for attempt in range(2):
                if self.requests >= 2048:
                    raise ValueError("Coverage request budget exhausted")
                self.requests += 1
                try:
                    request = Request(
                        url, headers={"User-Agent": "isometric-tokyo-coverage/1"}
                    )
                    with self.opener(request, timeout=45) as response:
                        length = response.headers.get("Content-Length")
                        expected = int(length) if length is not None else None
                        remaining = min(
                            self.max_bytes - self.network_bytes,
                            self.max_bytes - self.source_bytes,
                        )
                        if expected is not None and not 0 <= expected <= remaining:
                            raise ValueError(
                                "Coverage asset exceeds remaining byte budget"
                            )
                        blocks, size = [], 0
                        while expected is None or size < expected:
                            remaining = min(
                                self.max_bytes - self.network_bytes,
                                self.max_bytes - self.source_bytes - size,
                            )
                            if remaining <= 0:
                                raise ValueError(
                                    "Coverage byte budget exhausted before confirmed EOF"
                                )
                            block = response.read(
                                min(
                                    65536,
                                    remaining,
                                    expected - size
                                    if expected is not None
                                    else remaining,
                                )
                            )
                            self.network_bytes += len(block)
                            size += len(block)
                            if not block:
                                if expected is not None and size != expected:
                                    raise URLError("Truncated coverage response")
                                break
                            blocks.append(block)
                        blob = b"".join(blocks)
                    partial = raw_path.with_suffix(".part")
                    partial.write_bytes(blob)
                    partial.replace(raw_path)
                    write_json(
                        meta_path,
                        {"url": url, "bytes": len(blob), "sha256": digest(blob)},
                    )
                    break
                except HTTPError as exc:
                    if attempt or (exc.code != 429 and exc.code < 500):
                        raise
                except (URLError, TimeoutError):
                    if attempt:
                        raise
            else:
                raise RuntimeError("Coverage download did not complete")
        if key not in self.seen:
            self.source_bytes += len(blob)
            self.seen.add(key)
        return blob


def enu_geographic_bounds(box, heights, origin):
    """Bound a small local study box, with dense edge samples and a 2m guard."""
    if (
        len(box) != 4
        or len(heights) != 2
        or len(origin) != 2
        or any(
            type(v) not in (int, float) or not math.isfinite(v)
            for v in [*box, *heights, *origin]
        )
        or not box[0] < box[2]
        or not box[1] < box[3]
        or heights[0] > heights[1]
        or max(abs(v) for v in box) > 20_000
        or max(box[2] - box[0], box[3] - box[1]) > 20_000
        or abs(origin[1]) > 60
    ):
        raise ValueError(
            "Coverage audit requires finite local study extents within 20km and latitude60"
        )
    points = [
        local_to_geodetic((x, y, height), *origin)
        for x in [box[0] + (box[2] - box[0]) * i / 16 for i in range(17)]
        for y in [box[1] + (box[3] - box[1]) * i / 16 for i in range(17)]
        for height in heights
    ]
    lon_guard = 2 / (111000 * math.cos(math.radians(origin[1])))
    lat_guard = 2 / 110000
    return [
        min(p[0] for p in points) - lon_guard,
        min(p[1] for p in points) - lat_guard,
        max(p[0] for p in points) + lon_guard,
        max(p[1] for p in points) + lat_guard,
    ]


def region_enu_bounds(region, origin):
    """Conservative ENU AABB from a curved WGS84 region, not just its corners.

    Bilinear corner interpolation error is bounded by a deliberately loose
    10Mm second-derivative norm times angular side squared / 8 at each height.
    WGS84 ECEF is linear in height. The same vector-norm bound applies after
    orthonormal ENU rotation. Restrict the adapter to local, nonwrapped regions.
    """
    if len(region) != 6 or any(
        type(v) not in (int, float) or not math.isfinite(v) for v in region
    ):
        raise ValueError("Tile region must have six finite numbers")
    west, south, east, north, low, high = region
    if not (
        -math.pi <= west < east <= math.pi
        and -math.pi / 2 <= south < north <= math.pi / 2
        and low <= high
    ):
        raise ValueError("Invalid nonwrapped tile region")
    if east - west > 0.1 or north - south > 0.1 or max(abs(low), abs(high)) > 10000:
        raise ValueError("Tile region exceeds local curvature-bound assumptions")
    points = [
        local_point(math.degrees(lon), math.degrees(lat), height, *origin)
        for lon, lat, height in itertools.product(
            (west, east), (south, north), (low, high)
        )
    ]
    error = (
        (10_000_000 + max(abs(low), abs(high)))
        * ((east - west) ** 2 + (north - south) ** 2)
        / 8
    )
    return [min(p[axis] for p in points) - error for axis in range(3)] + [
        max(p[axis] for p in points) + error for axis in range(3)
    ]


def sun_slopes(rotation_degrees=(20, -25, -35), angular_diameter_degrees=12):
    """Exact component slope bounds for the SUN light's spherical direction cone."""
    if len(rotation_degrees) != 3 or any(
        type(v) not in (int, float) or not math.isfinite(v) for v in rotation_degrees
    ):
        raise ValueError("Three finite Euler angles are required")
    if (
        type(angular_diameter_degrees) not in (int, float)
        or not math.isfinite(angular_diameter_degrees)
        or not 0 <= angular_diameter_degrees <= 30
    ):
        raise ValueError("Sun angular diameter must be in [0,30] degrees")
    x, y, z = map(math.radians, rotation_degrees)
    # Blender XYZ Euler rotation of local +Z, the direction toward the light.
    direction = (
        math.cos(z) * math.sin(y) * math.cos(x) + math.sin(z) * math.sin(x),
        math.sin(z) * math.sin(y) * math.cos(x) - math.cos(z) * math.sin(x),
        math.cos(y) * math.cos(x),
    )
    radius = math.sin(math.radians(angular_diameter_degrees / 2))
    if direction[2] <= radius:
        raise ValueError(
            "Sun cone touches the horizon; finite shadow audit unavailable"
        )
    denominator = direction[2] ** 2 - radius**2
    slopes = []
    for axis in (0, 1):
        spread = radius * math.sqrt(
            direction[axis] ** 2 + direction[2] ** 2 - radius**2
        )
        center = direction[axis] * direction[2]
        slopes.append(
            ((center - spread) / denominator, (center + spread) / denominator)
        )
    reach_ratio = math.tan(
        math.acos(direction[2]) + math.radians(angular_diameter_degrees / 2)
    )
    return slopes, reach_ratio


def pixel_box(config, box):
    points = [
        world_to_pixel(config, point)
        for point in itertools.product(*[(box[i], box[i + 3]) for i in range(3)])
    ]
    return [
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    ]


def overlaps(a, b):
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


def classify_tile(config, tile, view, receiver_heights, slopes):
    bounds = region_enu_bounds(tile["bounding_volume"]["region"], config.origin_lonlat)
    direct = overlaps(pixel_box(config, bounds), view)
    # Source-minus-receiver heights. Independent coordinate intervals are
    # conservative: they can overselect but cannot omit a possible caster.
    height = max(0, bounds[5] - receiver_heights[0])
    shadow = False
    if bounds[5] >= receiver_heights[0]:
        offsets = [
            (min(0, lo * height, hi * height), max(0, lo * height, hi * height))
            for lo, hi in slopes
        ]
        shadow_box = [
            bounds[0] - offsets[0][1],
            bounds[1] - offsets[1][1],
            receiver_heights[0],
            bounds[3] - offsets[0][0],
            bounds[4] - offsets[1][0],
            min(receiver_heights[1], bounds[5]),
        ]
        shadow = overlaps(pixel_box(config, shadow_box), view)
    return {"direct_view": direct, "shadow_influence": shadow, "bounds_enu_m": bounds}


def building_items(snapshot):
    if snapshot.get("schema_version") != 1 or len(snapshot.get("wards", [])) != 23:
        raise ValueError("Coverage plan requires the verified 23-ward inventory")
    result = []
    for ward in snapshot["wards"]:
        if ward.get("metadata_status") != "confirmed" or not ward.get(
            "package", {}
        ).get("license", {}).get("url"):
            raise ValueError("Every ward needs confirmed identity and source terms")
        choices = [
            item
            for item in ward["catalog"]["items"]
            if item["type"] == "bldg"
            and item["format"] == "CESIUM3DTILES"
            and item["lod"] == 2
            and item["texture"] == "TEXTURE"
        ]
        if len(choices) != 1:
            raise ValueError("Expected one textured LOD2 building item per ward")
        result.append((ward, choices[0]))
    if (
        len({ward["code"] for ward, _ in result}) != 23
        or len({item["url"] for _, item in result}) != 23
    ):
        raise ValueError(
            "Coverage inventory must contain 23 distinct wards and source URLs"
        )
    return result


def make_plan(
    config_path,
    inventory_path,
    cache,
    *,
    receiver_heights=(0, 150),
    rotation=(20, -25, -35),
    sun_angle=12,
):
    config = load_config(config_path)
    if (
        len(receiver_heights) != 2
        or not all(
            type(v) in (int, float) and math.isfinite(v) for v in receiver_heights
        )
        or not config.vertical_range_enu_m[0]
        <= receiver_heights[0]
        < receiver_heights[1]
        <= config.vertical_range_enu_m[1]
    ):
        raise ValueError(
            "Receiver heights must be a finite interval within the configured height range"
        )
    slopes, reach_ratio = sun_slopes(rotation, sun_angle)
    shadow_receiver_heights = config.vertical_range_enu_m
    required_buffer = (
        config.vertical_range_enu_m[1] - shadow_receiver_heights[0]
    ) * reach_ratio
    if required_buffer > config.shadow_buffer_m:
        raise ValueError(
            "Configured shadow buffer is too small for this sun/height envelope"
        )
    over = config.overscan_px
    view = [
        config.bounds_px[0] - over,
        config.bounds_px[1] - over,
        config.bounds_px[2] + over,
        config.bounds_px[3] + over,
    ]
    full_box = source_footprint_enu(config, view)
    full_geo = enu_geographic_bounds(
        full_box, config.vertical_range_enu_m, config.origin_lonlat
    )
    ground_config = dataclasses.replace(
        config, vertical_range_enu_m=tuple(receiver_heights)
    )
    ground_box = source_footprint_enu(ground_config, view, buffer_m=0)
    inventory_raw = Path(inventory_path).read_bytes()
    sources, required = [], []
    for ward, item in building_items(json.loads(inventory_raw)):
        metadata = cache.fetch(item["url"])
        if len(metadata) > 5_000_000:
            raise ValueError("Tileset metadata exceeds five million bytes")
        data = json.loads(metadata)
        validate_tileset(data)
        candidates = select_leaf_tiles(data, full_geo)
        selected = []
        for tile in candidates:
            influence = classify_tile(
                config, tile, view, shadow_receiver_heights, slopes
            )
            if influence["direct_view"] or influence["shadow_influence"]:
                low, high = influence["bounds_enu_m"][2], influence["bounds_enu_m"][5]
                if (
                    low < config.vertical_range_enu_m[0]
                    or high > config.vertical_range_enu_m[1]
                ):
                    raise ValueError(
                        "Selected source bounding height exceeds configured envelope"
                    )
                record = {
                    **tile,
                    "url": validate_url(urljoin(item["url"], tile["uri"])),
                    "source_item_id": item["id"],
                    "ward_code": ward["code"],
                    "influence": influence,
                }
                required.append(record)
                selected.append(record)
        sources.append(
            {
                "ward_code": ward["code"],
                "ward_name": ward["name_en"],
                "edition": ward["edition"],
                "item": item,
                "package": ward["package"],
                "metadata_sha256": digest(metadata),
                "metadata_bytes": len(metadata),
                "candidate_tiles": len(candidates),
                "required_tiles": len(selected),
            }
        )
    if not 1 <= len(required) <= MAX_TILES or len(
        {tile["url"] for tile in required}
    ) != len(required):
        raise ValueError(
            "Expected unique required tile URLs within the 1024-tile limit"
        )
    plan = {
        "schema_version": 1,
        "kind": "source-coverage-plan",
        "algorithm": "region-volume-and-sun-cone-v1",
        "config": config.to_dict(),
        "config_sha256": config.fingerprint,
        "inventory_sha256": digest(inventory_raw),
        "view_bounds_px_with_overscan": view,
        "receiver_height_range_enu_m": list(receiver_heights),
        "shadow_receiver_height_range_enu_m": list(shadow_receiver_heights),
        "receiver_height_range_verified": False,
        "sun_euler_xyz_degrees": list(rotation),
        "sun_angular_diameter_degrees": sun_angle,
        "sun_slope_bounds": slopes,
        "maximum_shadow_reach_m": required_buffer,
        "broad_phase_bounds_enu_m": full_box,
        "broad_phase_bounds_wsen": full_geo,
        "ground_view_bounds_enu_m": ground_box,
        "ground_view_bounds_wsen": enu_geographic_bounds(
            ground_box, receiver_heights, config.origin_lonlat
        ),
        "sources": sources,
        "required_tiles": sorted(required, key=lambda tile: tile["url"]),
        "tile_count": len(required),
        "source_coverage_verified": False,
        "limitations": [
            "Official metadata bounding volumes are assumed truthful; payload geometry needs downstream checks.",
            "Selection completeness is not real-world feature completeness or acquisition-date agreement.",
            "Shadow selection covers the recorded directional sun cone over the full configured height interval, including building receivers; diffuse/global-light transport is not bounded by this proof.",
            "Receiver heights, terrain support and the actual render lighting must be checked against this plan before use.",
        ],
    }
    plan["id"] = fingerprint(plan)
    return plan


def load_plan(path):
    plan = json.loads(Path(path).read_text())
    if (
        plan.get("kind") != "source-coverage-plan"
        or plan.get("schema_version") != 1
        or fingerprint({k: v for k, v in plan.items() if k != "id"}) != plan.get("id")
    ):
        raise ValueError("Coverage plan identity mismatch")
    return plan


def existing_records(manifests):
    records = {}
    for manifest_path in manifests:
        root = Path(manifest_path).resolve().parent
        data = json.loads(Path(manifest_path).read_text())
        for tile in data["tiles"]:
            path = (root / tile["path"]).resolve()
            if (
                not path.is_relative_to(root)
                or not path.is_file()
                or path.stat().st_size != tile["bytes"]
                or digest(path.read_bytes()) != tile["sha256"]
            ):
                raise ValueError("Existing source tile checksum/path mismatch")
            if (
                tile["url"] in records
                and records[tile["url"]][1]["sha256"] != tile["sha256"]
            ):
                raise ValueError("Conflicting cached payloads for the same source URL")
            records[tile["url"]] = (path, tile)
    return records


def audit_manifest(plan, manifest_path):
    records = existing_records([manifest_path])
    required = {tile["url"] for tile in plan["required_tiles"]}
    present = required & set(records)
    mismatches = []
    for tile in plan["required_tiles"]:
        if tile["url"] in present and any(
            records[tile["url"]][1].get(key) != tile.get(key)
            for key in ("transform", "bounding_volume", "tile_bounding_volume")
        ):
            mismatches.append(tile["url"])
    missing = sorted(required - set(records))
    return {
        "schema_version": 1,
        "kind": "source-coverage-audit",
        "coverage_plan_id": plan["id"],
        "building_manifest_sha256": digest(Path(manifest_path).read_bytes()),
        "config_sha256": plan.get("config_sha256"),
        "canonical_config": plan.get("config"),
        "view_bounds_px_with_overscan": plan.get("view_bounds_px_with_overscan"),
        "sun_euler_xyz_degrees": plan.get("sun_euler_xyz_degrees"),
        "sun_angular_diameter_degrees": plan.get("sun_angular_diameter_degrees"),
        "shadow_receiver_height_range_enu_m": plan.get(
            "shadow_receiver_height_range_enu_m"
        ),
        "inventory_sha256": plan.get("inventory_sha256"),
        "examined_ward_count": len(plan.get("sources", [])),
        "selected_sources": [
            {
                "ward_code": s["ward_code"],
                "ward_name": s["ward_name"],
                "edition": s["edition"],
                "item_id": s["item"]["id"],
                "tileset_url": s["item"]["url"],
                "metadata_sha256": s["metadata_sha256"],
                "required_tiles": s["required_tiles"],
                "package": s["package"],
            }
            for s in plan.get("sources", [])
            if s["required_tiles"]
        ],
        "limitations": plan.get("limitations", []),
        "required_tiles": len(required),
        "present_tiles": len(present),
        "missing_tiles": len(missing),
        "missing_urls": missing,
        "extra_tiles": len(set(records) - required),
        "metadata_mismatch_urls": mismatches,
        "tile_selection_complete": not missing and not mismatches,
        "source_coverage_verified": False,
    }


def contains(outer, inner):
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )


def terrain_support(plan, mesh_path):
    """Check an oriented terrain TIN has only perimeter boundaries and full area.

    This tests supplied surface support, not whether the terrain survey is accurate.
    It intentionally does not turn the broad source-completeness flag on.
    """
    raw = Path(mesh_path).read_bytes()
    mesh = json.loads(raw)
    config = parse_config(plan["config"])
    vertices, triangles = mesh["vertices_lon_lat_height"], mesh["triangles"]
    if (
        not vertices
        or len(vertices) > 500000
        or not triangles
        or len(triangles) > 1000000
    ):
        raise ValueError("Terrain geometry exceeds audit budget or is empty")
    if any(
        len(point) != 3
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in point)
        for point in vertices
    ):
        raise ValueError("Terrain contains invalid coordinates")
    bounds = [
        min(v[0] for v in vertices),
        min(v[1] for v in vertices),
        max(v[0] for v in vertices),
        max(v[1] for v in vertices),
    ]
    edges, area = {}, 0.0
    for triangle in triangles:
        if len(triangle) != 3 or any(
            type(i) is not int or not 0 <= i < len(vertices) for i in triangle
        ):
            raise ValueError("Invalid terrain triangle indices")
        a, b, c = [vertices[i] for i in triangle]
        signed = ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2
        if signed <= 0:
            raise ValueError("Terrain has reversed or degenerate triangles")
        area += signed
        for a, b in zip(triangle, triangle[1:] + triangle[:1], strict=True):
            key = (min(a, b), max(a, b))
            edges.setdefault(key, []).append(1 if a < b else -1)
    boundary_count = 0
    for (a, b), signs in edges.items():
        if len(signs) == 2 and sum(signs) == 0:
            continue
        if len(signs) != 1:
            raise ValueError("Terrain has nonmanifold or inconsistently oriented edges")
        boundary_count += 1
        left, right = vertices[a], vertices[b]
        if not any(
            abs(left[axis] - value) < 1e-10 and abs(right[axis] - value) < 1e-10
            for axis, value in (
                (0, bounds[0]),
                (1, bounds[1]),
                (0, bounds[2]),
                (1, bounds[3]),
            )
        ):
            raise ValueError("Terrain has an open interior edge")
    expected = (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])
    if expected <= 0 or abs(area - expected) > expected * 1e-7:
        raise ValueError("Terrain triangle area does not cover its rectangle")
    heights = [local_point(*point, *config.origin_lonlat)[2] for point in vertices]
    height_range = [min(heights), max(heights)]
    planned = plan["receiver_height_range_enu_m"]
    verified_height = planned[0] <= height_range[0] and planned[1] >= height_range[1]
    ground_config = dataclasses.replace(
        config, vertical_range_enu_m=tuple(height_range)
    )
    needed_enu = source_footprint_enu(
        ground_config, plan["view_bounds_px_with_overscan"], buffer_m=0
    )
    needed = enu_geographic_bounds(needed_enu, height_range, config.origin_lonlat)
    return {
        "mesh_sha256": digest(raw),
        "vertex_count": len(vertices),
        "triangle_count": len(triangles),
        "bounds_wsen": bounds,
        "required_bounds_wsen": needed,
        "height_range_enu_m": height_range,
        "receiver_height_range_verified": verified_height,
        "terrain_surface_support_complete": verified_height
        and contains(bounds, needed),
        "boundary_edges": boundary_count,
        "source_coverage_verified": False,
    }


def orthophoto_support(manifest_path, required_bounds):
    from fetch_ground import tile_bounds, tile_to_lonlat
    from PIL import Image

    root = Path(manifest_path).resolve().parent
    raw = Path(manifest_path).read_bytes()
    data = json.loads(raw)
    zoom, limits = data["zoom"], data["tile_range"]
    xmin, ymin, xmax, ymax = limits
    if (
        any(type(v) is not int for v in [zoom, *limits])
        or not 0 <= zoom <= 19
        or xmin > xmax
        or ymin > ymax
        or (xmax - xmin + 1) * (ymax - ymin + 1) > 400
    ):
        raise ValueError("Invalid bounded orthophoto grid")
    required = {(x, y) for x in range(xmin, xmax + 1) for y in range(ymin, ymax + 1)}
    actual = set()
    for tile in data["tiles"]:
        coordinate = tile["x"], tile["y"]
        if (
            coordinate in actual
            or coordinate not in required
            or tile["z"] != zoom
            or list(tile["bounds_wsen"]) != list(tile_bounds(*coordinate, zoom))
        ):
            raise ValueError("Orthophoto tile grid mismatch")
        actual.add(coordinate)
        path = (root / tile["path"]).resolve()
        if (
            not path.is_relative_to(root)
            or not path.is_file()
            or path.stat().st_size != tile["bytes"]
            or digest(path.read_bytes()) != tile["sha256"]
        ):
            raise ValueError("Orthophoto source checksum/path mismatch")
    west, north = tile_to_lonlat(xmin, ymin, zoom)
    east, south = tile_to_lonlat(xmax + 1, ymax + 1, zoom)
    bounds = [west, south, east, north]
    if data["bounds_wsen"] != bounds:
        raise ValueError("Orthophoto declared bounds mismatch")
    image = (root / data["image"]).resolve()
    if (
        not image.is_relative_to(root)
        or not image.is_file()
        or digest(image.read_bytes()) != data.get("image_sha256")
    ):
        raise ValueError("Orthophoto mosaic checksum/path mismatch")
    size = ((xmax - xmin + 1) * 256, (ymax - ymin + 1) * 256)
    with Image.open(image) as mosaic:
        if (
            mosaic.size != size
            or data.get("image_width") != size[0]
            or data.get("image_height") != size[1]
        ):
            raise ValueError("Orthophoto mosaic dimensions mismatch")
        mosaic.load()
        for tile in data["tiles"]:
            x, y = (tile["x"] - xmin) * 256, (tile["y"] - ymin) * 256
            with Image.open(root / tile["path"]) as source:
                if (
                    source.size != (256, 256)
                    or mosaic.crop((x, y, x + 256, y + 256)).convert("RGB").tobytes()
                    != source.convert("RGB").tobytes()
                ):
                    raise ValueError(
                        "Orthophoto mosaic pixels do not match source grid"
                    )
    return {
        "manifest_sha256": digest(raw),
        "tile_count": len(actual),
        "bounds_wsen": bounds,
        "orthophoto_support_complete": actual == required
        and contains(bounds, required_bounds),
        "source_coverage_verified": False,
    }


def probe_sizes(plan, cache, manifests=()):
    existing = existing_records(manifests)
    sizes, unknown = {}, []
    for tile in plan["required_tiles"]:
        url = tile["url"]
        if url in existing:
            sizes[url] = existing[url][1]["bytes"]
            continue
        path = cache.root / (digest(url.encode()) + ".head.json")
        if path.exists():
            record = json.loads(path.read_text())
            if record.get("url") != url:
                raise ValueError("Cached HEAD URL mismatch")
        else:
            if cache.requests >= 2048:
                raise ValueError("Coverage request budget exhausted")
            cache.requests += 1
            with cache.opener(
                Request(validate_url(url), method="HEAD"), timeout=45
            ) as response:
                length = response.headers.get("Content-Length")
                record = {
                    "url": url,
                    "bytes": int(length) if length else None,
                    "etag": response.headers.get("ETag"),
                }
            write_json(path, record)
        if type(record["bytes"]) is int and record["bytes"] >= 0:
            sizes[url] = record["bytes"]
        else:
            unknown.append(url)
    total = sum(sizes.values())
    reused = sum(sizes[url] for url in sizes if url in existing)
    return {
        "coverage_plan_id": plan["id"],
        "tile_count": plan["tile_count"],
        "known_payload_bytes": total,
        "reusable_payload_bytes": reused,
        "estimated_new_payload_bytes": total - reused,
        "unknown_size_count": len(unknown),
        "unknown_urls": unknown,
        "per_ward": {
            source["ward_code"]: {
                "tiles": source["required_tiles"],
                "known_bytes": sum(
                    sizes.get(tile["url"], 0)
                    for tile in plan["required_tiles"]
                    if tile["ward_code"] == source["ward_code"]
                ),
            }
            for source in plan["sources"]
            if source["required_tiles"]
        },
        "note": "HEAD lengths are preflight estimates, not trusted payload checksums. The acquisition byte budget remains enforced while reading.",
    }


def acquire(plan, output, cache, manifests=()):
    existing = existing_records(manifests)
    # Seed only previously checksummed original payloads, never unverified cache bytes.
    cache.root.mkdir(parents=True, exist_ok=True)
    for tile in plan["required_tiles"]:
        if tile["url"] in existing:
            path, record = existing[tile["url"]]
            key = digest(tile["url"].encode())
            raw, meta = cache.root / (key + ".bin"), cache.root / (key + ".json")
            if not raw.exists() and not meta.exists():
                shutil.copyfile(path, raw)
                write_json(
                    meta,
                    {
                        "url": tile["url"],
                        "bytes": record["bytes"],
                        "sha256": record["sha256"],
                    },
                )
    output = Path(output)
    if output.is_symlink() or (output.exists() and not (output / MARKER).is_file()):
        raise ValueError("Refusing to replace an unknown coverage source directory")
    backup = output.with_name("." + output.name + "-previous")
    if backup.exists():
        raise ValueError("Previous coverage output backup exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".coverage-stage-", dir=output.parent))
    try:
        (staging / MARKER).write_text("Bounded source coverage acquisition v1\n")
        write_json(staging / "coverage-plan.json", plan)
        sources = []
        items = {}
        for source in plan["sources"]:
            metadata = cache.fetch(source["item"]["url"])
            if digest(metadata) != source["metadata_sha256"]:
                raise ValueError("Source tileset changed after coverage planning")
            relative = f"metadata/{source['ward_code']}-tileset.json"
            (staging / relative).parent.mkdir(parents=True, exist_ok=True)
            (staging / relative).write_bytes(metadata)
            sources.append({**source, "metadata_path": relative})
            items[source["item"]["id"]] = source["item"]
        tiles, identifiers = [], set()
        for number, tile in enumerate(plan["required_tiles"], 1):
            record = context_tile(tile, items[tile["source_item_id"]], cache, staging)
            record.pop("context_type")
            ids = record["feature_ids"].get("gml_id")
            if (
                not isinstance(ids, list)
                or len(ids) != record["feature_count"]
                or any(not isinstance(v, str) or not v for v in ids)
            ):
                raise ValueError("Building tile lacks a complete gml_id table")
            if len(ids) != len(set(ids)) or identifiers.intersection(ids):
                raise ValueError(
                    "Duplicate source gml_id would double-render a building"
                )
            identifiers.update(ids)
            record["building_count"] = record["feature_count"]
            tiles.append(record)
            print(
                f"[{number}/{len(plan['required_tiles'])}] {tile['ward_code']}: {record['bytes']:,} bytes",
                flush=True,
            )
        config = plan["config"]
        manifest = {
            "schema_version": 1,
            "kind": "coverage-building-sample",
            "status": "complete",
            "title": "Shinjuku camera-influence building source study",
            "coverage_plan_id": plan["id"],
            "coverage_plan_path": "coverage-plan.json",
            "source_date": "Catalog editions "
            + ", ".join(
                str(v)
                for v in sorted(
                    {
                        source["edition"]
                        for source in sources
                        if source["required_tiles"]
                    }
                )
            )
            + "; acquisition dates are not assumed equal",
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "sources": sources,
            "attribution": "Tokyo Metropolitan Government / Project PLATEAU. "
            + ", ".join(
                f"{source['ward_name']} {source['edition']} catalog edition"
                for source in sources
                if source["required_tiles"]
            )
            + ". Original building tiles retained; no geometry changes.",
            "license": {
                "url": "https://www.mlit.go.jp/plateau/site-policy/",
                "note": "Per-ward source package terms are retained in sources. Geographic data is not relicensed under the code licence.",
            },
            "origin": {
                "lon": config["origin_lonlat"][0],
                "lat": config["origin_lonlat"][1],
                "height": 0,
            },
            "bbox": plan["broad_phase_bounds_wsen"],
            "selection": "Official region-volume overlap with recorded camera view or directional-sun shadow influence. Entire original tiles retained.",
            "tile_count": len(tiles),
            "building_count": len(identifiers),
            "download_bytes": cache.network_bytes,
            "source_bytes": cache.source_bytes,
            "byte_budget": cache.max_bytes,
            "tile_selection_complete": True,
            "source_coverage_verified": False,
            "receiver_height_range_verified": False,
            "tiles": tiles,
        }
        write_json(staging / "manifest.json", manifest)
        if output.exists():
            output.rename(backup)
        try:
            staging.rename(output)
        except BaseException:
            if backup.exists():
                backup.rename(output)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--config", type=Path, default=Path("configs/shinjuku.json"))
    plan.add_argument(
        "--inventory",
        type=Path,
        default=Path("configs/sources-tokyo-2025.snapshot.json"),
    )
    plan.add_argument("--receiver-heights", nargs=2, type=float, default=[0, 150])
    plan.add_argument("--output", type=Path, default=Path(".cache/coverage/plan.json"))
    for name in ("probe", "acquire", "audit"):
        command = sub.add_parser(name)
        command.add_argument(
            "--plan", type=Path, default=Path(".cache/coverage/plan.json")
        )
        if name == "audit":
            command.add_argument(
                "--manifest",
                type=Path,
                default=Path("data/coverage-buildings/manifest.json"),
            )
            command.add_argument("--terrain", type=Path)
            command.add_argument("--ground", type=Path)
            command.add_argument("--output", type=Path)
        else:
            command.add_argument(
                "--reuse",
                type=Path,
                nargs="*",
                default=[Path("data/sample/manifest.json")],
            )
        if name == "probe":
            command.add_argument(
                "--output", type=Path, default=Path(".cache/coverage/sizes.json")
            )
        if name == "acquire":
            command.add_argument(
                "--output", type=Path, default=Path("data/coverage-buildings")
            )
    for command in (plan, *[sub.choices[name] for name in ("probe", "acquire")]):
        command.add_argument(
            "--cache", type=Path, default=Path(".cache/coverage-metadata")
        )
        command.add_argument("--max-bytes", type=int, default=MAX_BYTES)
    args = parser.parse_args()
    if args.action == "audit":
        plan = load_plan(args.plan)
        result = audit_manifest(plan, args.manifest)
        if args.ground and not args.terrain:
            parser.error(
                "--ground requires --terrain to establish actual visible heights"
            )
        if args.terrain:
            result["terrain"] = terrain_support(plan, args.terrain)
        if args.ground:
            result["orthophoto"] = orthophoto_support(
                args.ground, result["terrain"]["required_bounds_wsen"]
            )
        if args.output:
            write_json(args.output, result)
    else:
        cache = CoverageCache(args.cache, args.max_bytes)
        if args.action == "plan":
            result = make_plan(
                args.config,
                args.inventory,
                cache,
                receiver_heights=args.receiver_heights,
            )
            write_json(args.output, result)
            result = {
                "plan_id": result["id"],
                "required_tiles": result["tile_count"],
                "per_ward": {
                    s["ward_code"]: s["required_tiles"]
                    for s in result["sources"]
                    if s["required_tiles"]
                },
                "metadata_bytes": cache.source_bytes,
                "ground_bounds_wsen": result["ground_view_bounds_wsen"],
            }
        elif args.action == "probe":
            result = probe_sizes(load_plan(args.plan), cache, args.reuse)
            write_json(args.output, result)
        else:
            result = acquire(load_plan(args.plan), args.output, cache, args.reuse)
            result = {
                key: result[key]
                for key in (
                    "tile_count",
                    "building_count",
                    "download_bytes",
                    "source_bytes",
                    "tile_selection_complete",
                    "source_coverage_verified",
                )
            }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

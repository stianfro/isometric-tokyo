"""Drape source-located vegetation representations without claiming surveyed trees."""

from __future__ import annotations

import hashlib
import json
import math

try:
    from .geo import local_point
    from .render_context import SHA256, asset_path
except ImportError:
    from geo import local_point
    from render_context import SHA256, asset_path


def load_vegetation(path):
    if path.stat().st_size > 64_000_000:
        raise ValueError("Vegetation manifest exceeds 64 MB input budget")
    data = json.loads(path.read_text())
    if data.get("schema_version") != 1 or data.get("kind") != "source-vegetation":
        raise ValueError("Expected a source-vegetation manifest")
    samples = data.get("canopy_samples", [])
    if not isinstance(samples, list) or len(samples) > 2000:
        raise ValueError("Vegetation sample count exceeds the bounded renderer limit")
    identities = set()
    for sample in samples:
        identity = sample.get("id")
        if not isinstance(identity, str) or not identity or identity in identities:
            raise ValueError("Vegetation sample IDs must be nonempty and unique")
        identities.add(identity)
        if sample.get("placement") not in ("mapped_tree", "artistic_woodland_fill"):
            raise ValueError("Unsupported vegetation placement evidence")
        if sample.get("dimensions_measured") is not False:
            raise ValueError(
                "This representation requires explicitly unmeasured dimensions"
            )
        if not isinstance(sample.get("source_id"), str) or not sample["source_id"]:
            raise ValueError("Vegetation sample requires an original mapped source ID")
        for field, low, high in (
            ("lon", -180, 180),
            ("lat", -90, 90),
            ("radius_m", 0.2, 20),
            ("height_m", 0.5, 40),
        ):
            value = sample.get(field)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not low <= value <= high
            ):
                raise ValueError(
                    "Vegetation coordinate or display dimension is invalid"
                )
    total_bytes = path.stat().st_size
    for asset in data.get("assets", []):
        target = asset_path(path.parent, asset["path"])
        checksum = asset.get("sha256")
        if not isinstance(checksum, str) or not SHA256.fullmatch(checksum):
            raise ValueError("Vegetation source asset requires a SHA256 checksum")
        total_bytes += target.stat().st_size
        if total_bytes > 96_000_000:
            raise ValueError("Vegetation source assets exceed 96 MB total budget")
        if hashlib.sha256(target.read_bytes()).hexdigest() != checksum:
            raise ValueError("Vegetation source asset checksum mismatch")
    return data


def vegetation_identity(path):
    data = load_vegetation(path)
    return {
        "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "assets": {asset["path"]: asset["sha256"] for asset in data.get("assets", [])},
    }


def vegetation_provenance(data):
    source = data.get("source", {})
    return {
        "source_attribution": source.get("attribution", data.get("attribution")),
        "source_license": source.get("license", data.get("license")),
        "source": source,
        "sources": data.get("sources", []),
        "public_data_reference": data.get(
            "public_data_reference", source.get("public_data_reference")
        ),
    }


def attach_vegetation(path, ground, config):
    """Return provenance; objects get vegetation semantics and explicit art parts."""
    import bpy
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    data = load_vegetation(path)
    vertices = [ground.matrix_world @ vertex.co for vertex in ground.data.vertices]
    ground.data.calc_loop_triangles()
    triangles = [tuple(item.vertices) for item in ground.data.loop_triangles]
    tree = BVHTree.FromPolygons(vertices, triangles, all_triangles=True, epsilon=0)
    top = max(vertex.z for vertex in vertices) + 1000
    length = top - min(vertex.z for vertex in vertices) + 1000
    materials = {}
    for part, color in (
        ("canopy", (0.20, 0.31, 0.17)),
        ("trunk", (0.25, 0.19, 0.12)),
        ("cover", (0.31, 0.38, 0.22)),
    ):
        material = bpy.data.materials.new("Mapped vegetation representation: " + part)
        material.use_nodes = True
        material.node_tree.nodes["Principled BSDF"].inputs[
            "Base Color"
        ].default_value = (*color, 1)
        materials[part] = material
    # Build prototypes once. Repeated primitive operators trigger costly scene-wide
    # dependency updates for thousands of crowns; linked meshes retain the same
    # geometry with per-instance transforms and one final dependency update.
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=1)
    prototype = bpy.context.object
    crown_mesh = prototype.data
    bpy.data.objects.remove(prototype, do_unlink=True)
    crown_mesh.materials.append(materials["canopy"])
    bpy.ops.mesh.primitive_cone_add(vertices=6, radius1=1, radius2=2 / 3, depth=1)
    prototype = bpy.context.object
    trunk_mesh = prototype.data
    bpy.data.objects.remove(prototype, do_unlink=True)
    trunk_mesh.materials.append(materials["trunk"])
    counts = {"mapped_tree": 0, "artistic_woodland_fill": 0, "outside_terrain": 0}
    for sample in data["canopy_samples"]:
        x, y, _ = local_point(sample["lon"], sample["lat"], 0, *config.origin_lonlat)
        base, _, _, _ = tree.ray_cast(Vector((x, y, top)), Vector((0, 0, -1)), length)
        if base is None:
            counts["outside_terrain"] += 1
            continue
        radius, height = sample["radius_m"], sample["height_m"]
        crown = bpy.data.objects.new("Mapped canopy", crown_mesh)
        crown.location = (x, y, base.z + height * 0.65)
        crown.scale = (radius, radius, height * 0.35)
        trunk = bpy.data.objects.new("Mapped trunk", trunk_mesh)
        trunk.location = (x, y, base.z + height * 0.275)
        trunk.scale = (
            max(0.12, radius * 0.06),
            max(0.12, radius * 0.06),
            height * 0.55,
        )
        bpy.context.collection.objects.link(crown)
        bpy.context.collection.objects.link(trunk)
        for obj, part in ((crown, "canopy"), (trunk, "trunk")):
            obj.name = f"Mapped vegetation {sample['id']} {part}"
            obj["atlas_semantic"] = "vegetation"
            obj["vegetation_part"] = part
            obj["atlas_direct_feature_key"] = sample["id"]
            obj["vegetation_source_id"] = sample["source_id"]
            obj["vegetation_placement"] = sample["placement"]
            obj["vegetation_dimensions_measured"] = False
        counts[sample["placement"]] += 1
    bpy.context.view_layer.update()
    return {
        "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source_kind": "Open mapped features, not a tree survey",
        **vegetation_provenance(data),
        "representation_counts": counts,
        "dimensions_measured": False,
        "canopy_shape_measured": False,
        "terrain_draping": "Original shared terrain surface, no change to terrain elevations",
        "source_coverage_verified": False,
    }


def register_vegetation_objects(registry, objects):
    """Append explicit mapped/artistic instance identities, never building IDs."""
    records = {}
    for obj in objects:
        key = obj.get("atlas_direct_feature_key")
        if key:
            records[key] = {
                "kind": "vegetation",
                "source_id": obj["vegetation_source_id"],
                "representation_id": key,
                "placement": obj["vegetation_placement"],
                "dimensions_measured": False,
                "surveyed_tree": False,
            }
    ids = {}
    for key in sorted(records):
        identifier = len(registry["records"])
        if identifier >= 2**24:
            raise ValueError("Vegetation registry exceeds RGB24 ID capacity")
        ids[key] = identifier
        registry["records"].append({"id": identifier, **records[key]})
    registry["direct_feature_ids"] = ids
    return registry

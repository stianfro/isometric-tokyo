"""Validate bounded PLATEAU context inputs before adding them to a source scene."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath

CONTEXT_SEMANTICS = {"tran": "transportation", "wtr": "water", "veg": "vegetation"}
SEMANTIC_IDS = {
    "background": 0,
    "ground": 1,
    "building": 2,
    "transportation": 3,
    "water": 4,
    "vegetation": 5,
}
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def asset_path(root, name):
    if not isinstance(name, str) or "\\" in name:
        raise ValueError("Context asset must have a relative POSIX path")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("Context asset must stay within its source directory")
    target = root.joinpath(*path.parts).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("Context asset symlink escapes the source directory")
    return target


def context_assets(manifest_path):
    data = json.loads(manifest_path.read_text())
    if (
        data.get("schema_version") != 1
        or data.get("kind") != "plateau-context"
        or data.get("status") != "complete"
    ):
        raise ValueError("Expected a complete PLATEAU context manifest")
    if not isinstance(data.get("tiles"), list) or not data["tiles"]:
        raise ValueError("Context manifest contains no selected geometry")
    assets = []
    counts = {}
    for tile in data["tiles"]:
        kind = tile.get("context_type")
        if kind not in CONTEXT_SEMANTICS:
            raise ValueError("Unknown source context type")
        if tile.get("dependencies"):
            raise ValueError(
                "Context renderer requires self-contained GLBs; external dependencies need an import adapter"
            )
        counts[kind] = counts.get(kind, 0) + 1
        for path_field, hash_field in (
            ("path", "sha256"),
            ("glb_path", "glb_sha256"),
            ("tables_path", "tables_sha256"),
        ):
            checksum = tile.get(hash_field)
            if not isinstance(checksum, str) or not SHA256.fullmatch(checksum):
                raise ValueError("Context asset requires a SHA256 checksum")
            assets.append(
                (asset_path(manifest_path.parent, tile[path_field]), checksum)
            )
    sources = data.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError("Context sources must be a list")
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("Context source metadata must be an object")
        fields = ("metadata_path", "metadata_sha256")
        present = [field in source for field in fields]
        if not any(present):
            continue
        if not all(present):
            raise ValueError(
                "Context metadata path and SHA256 must be recorded together"
            )
        checksum = source["metadata_sha256"]
        if not isinstance(checksum, str) or not SHA256.fullmatch(checksum):
            raise ValueError("Context source metadata requires a SHA256 checksum")
        assets.append(
            (asset_path(manifest_path.parent, source["metadata_path"]), checksum)
        )
    return data, assets, counts


def verify_context_assets(assets):
    for path, checksum in assets:
        if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            raise ValueError("Context source checksum mismatch")


def separation_summary(values):
    """Summarize source-minus-terrain metres, without diagnosing or fixing them."""
    if not values:
        return None
    values = sorted(values)
    return {
        "minimum_m": values[0],
        "p10_m": values[round((len(values) - 1) * 0.1)],
        "median_m": values[round((len(values) - 1) * 0.5)],
        "p90_m": values[round((len(values) - 1) * 0.9)],
        "maximum_m": values[-1],
    }


def vertical_alignment_diagnostics(objects, ground, *, max_samples_per_kind=2048):
    """Bounded deterministic source-vertex samples against original terrain.

    A positive separation is above the terrain, not necessarily an error. Roads
    can be bridges and water need not coincide with a terrain surface. No vertex
    or material is changed by this diagnostic.
    """
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    vertices = [ground.matrix_world @ vertex.co for vertex in ground.data.vertices]
    ground.data.calc_loop_triangles()
    triangles = [tuple(item.vertices) for item in ground.data.loop_triangles]
    tree = BVHTree.FromPolygons(vertices, triangles, all_triangles=True, epsilon=0)
    heights = [point.z for point in vertices]
    ray_top = max(heights) + 10000
    ray_length = ray_top - min(heights) + 10000
    result = {
        "definition": "Source vertex ENU-up minus vertical terrain intersection ENU-up, in metres",
        "interpretation": "Descriptive separation only; bridges, water surfaces and source dates require review",
        "height_adjustment_m": 0,
        "reviewed": False,
        "terrain_height_range_enu_m": [min(heights), max(heights)],
        "sample_cap_per_semantic": max_samples_per_kind,
        "semantics": {},
    }
    for kind in sorted({obj["atlas_semantic"] for obj in objects}):
        group = [obj for obj in objects if obj["atlas_semantic"] == kind]
        total = sum(len(obj.data.vertices) for obj in group)
        samples = min(max_samples_per_kind, total)
        selected = {index * total // samples for index in range(samples)}
        offset, tested = 0, 0
        separations = []
        for obj in group:
            for vertex in obj.data.vertices:
                if offset in selected:
                    point = obj.matrix_world @ vertex.co
                    hit, _, _, _ = tree.ray_cast(
                        Vector((point.x, point.y, ray_top)),
                        Vector((0, 0, -1)),
                        ray_length,
                    )
                    tested += 1
                    if hit is not None:
                        separations.append(point.z - hit.z)
                offset += 1
        result["semantics"][kind] = {
            "source_vertex_count": total,
            "tested_vertex_count": tested,
            "terrain_intersection_count": len(separations),
            "outside_terrain_count": tested - len(separations),
            "source_minus_terrain_m": separation_summary(separations),
        }
    return result

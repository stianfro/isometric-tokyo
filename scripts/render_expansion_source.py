"""Native 5 by 5 Shinjuku source expansion on the existing atlas pixel grid.

The old source is retained for an overlap audit, not silently replaced or scaled.
Global pixel coordinates may be negative; PNG crop coordinates are always local.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atlas import camera_for_bounds, geographic_to_pixel, load_config  # noqa: E402
from render_pilot_source import (  # noqa: E402
    digest,
    finalize_partition,
    render_native,
    write_index,
)

INDEX = "expansion-source.json"
BOUNDS = (-1024, -1024, 4096, 4096)
SIZE = 5120
CORE = 1024
ROOT = Path(__file__).resolve().parents[1]


def grid_plan(core=CORE, context=256):
    if (
        type(core) is not int
        or not 1 <= core <= CORE
        or type(context) is not int
        or not 0 <= context <= core
    ):
        raise ValueError("Expansion requires finite native cores and bounded context")
    low, high = -core, 4 * core
    result = []
    for y in range(-1, 4):
        for x in range(-1, 4):
            bounds = [x * core, y * core, (x + 1) * core, (y + 1) * core]
            expanded = [
                max(low, bounds[0] - context),
                max(low, bounds[1] - context),
                min(high, bounds[2] + context),
                min(high, bounds[3] + context),
            ]
            result.append(
                {
                    "id": f"x{x}_y{y}",
                    "bounds_px": bounds,
                    "crop_bounds_px": [v - low for v in bounds],
                    "context_bounds_px": expanded,
                    "context_crop_bounds_px": [v - low for v in expanded],
                    "core_offset_in_context_px": [
                        bounds[0] - expanded[0],
                        bounds[1] - expanded[1],
                    ],
                    "retained_pilot_area": 0 <= x <= 2 and 0 <= y <= 2,
                }
            )
    return result


def validate_camera(config):
    previous = load_config(ROOT / "configs/shinjuku-pilot-source.json")
    stable = (
        "origin_lonlat",
        "camera_target_enu",
        "camera_offset_enu",
        "pixel_origin",
        "pixels_per_metre",
        "vertical_range_enu_m",
        "shadow_buffer_m",
        "render_samples",
        "render_seed",
    )
    if (
        config.bounds_px != BOUNDS
        or config.chunk_size_px != CORE
        or config.overscan_px != 0
        or any(getattr(config, key) != getattr(previous, key) for key in stable)
        or not math.isclose(SIZE / config.pixels_per_metre, 2100, abs_tol=1e-8)
    ):
        raise ValueError(
            "Expansion must preserve the pilot camera, pixel origin and density"
        )
    return camera_for_bounds(config, config.bounds_px)


def landmark_plan(config):
    """User-supplied approximate planning hints, never verified POI records."""
    hints = (
        ("Shinjuku Station", 139.7005, 35.6909),
        ("Kabukicho", 139.703, 35.695),
        ("Shinjuku Gyoen", 139.710, 35.685),
    )
    result = []
    for label, lon, lat in hints:
        x, y = geographic_to_pixel(config, lon, lat, 50)
        result.append(
            {
                "label": label,
                "approximate_lonlat": [lon, lat],
                "assumed_ellipsoidal_height_m": 50,
                "global_pixel": [x, y],
                "tile_id": f"x{math.floor(x / CORE)}_y{math.floor(y / CORE)}",
                "inside_expansion": BOUNDS[0] <= x < BOUNDS[2]
                and BOUNDS[1] <= y < BOUNDS[3],
                "verified": False,
            }
        )
    return result


def source_provenance(building_manifest, vegetation_manifest, public_dataset):
    building = json.loads(building_manifest.read_text())
    vegetation = json.loads(vegetation_manifest.read_text())
    return {
        "building_sources": [
            {
                "label": f"{source['ward_name']} FY{source['edition']} PLATEAU",
                "url": source["package"]["url"],
            }
            for source in building["sources"]
            if source["required_tiles"]
        ],
        "vegetation": {
            "snapshot_range": vegetation["source"]["snapshot_range"],
            "public_data_sha256": digest(public_dataset / "vegetation.json.gz"),
        },
        "context_coverage": "Original PLATEAU transportation, water and vegetation context is selected from Shinjuku only. Neighboring building wards do not imply matching context-model coverage. Orthophoto, native terrain and mapped OSM vegetation provide bounded context, not a complete survey.",
    }


def canonical_feature_key(record):
    kind = record["kind"]
    if record.get("gml_id"):
        return (kind, record["gml_id"])
    if record.get("representation_id"):
        return (kind, record["representation_id"])
    if record.get("source_tile_sha256"):
        return (kind, record["source_tile_sha256"])
    return (kind, "")


def guide_overlap(directory, previous_directory, old, new):
    """Compare stable feature identities, never release-local numeric ID values."""
    import numpy as np
    from PIL import Image
    from render_geometry import relative_asset

    registries, frames = [], []
    for root, receipt in ((previous_directory, old), (directory, new)):
        meta = receipt["guide_metadata"]["feature_id"]
        path = relative_asset(root, meta["registry"])
        if digest(path) != meta["registry_sha256"]:
            raise ValueError("Feature registry checksum mismatch during overlap audit")
        registry = json.loads(path.read_text())["records"]
        if [item["id"] for item in registry] != list(range(len(registry))):
            raise ValueError(
                "Feature registry must retain contiguous numeric lookup IDs"
            )
        registries.append(registry)
        asset = receipt["guide_assets"]["feature-id"]
        path = relative_asset(root, asset["path"])
        if digest(path) != asset["sha256"]:
            raise ValueError("Feature guide checksum mismatch during overlap audit")
        with Image.open(path) as opened:
            if root == directory:
                opened = opened.crop((1024, 1024, 4096, 4096))
            pixels = np.asarray(opened.convert("RGB"), dtype=np.uint32)
        ids = (pixels[:, :, 0] << 16) | (pixels[:, :, 1] << 8) | pixels[:, :, 2]
        if int(ids.max()) >= len(registry):
            raise ValueError("Feature guide ID outside its source registry")
        frames.append(ids)
    keys = {
        canonical_feature_key(record) for records in registries for record in records
    }
    lookup = {key: i for i, key in enumerate(sorted(keys))}
    stable = [
        np.array(
            [lookup[canonical_feature_key(record)] for record in records],
            dtype=np.uint32,
        )[ids]
        for records, ids in zip(registries, frames, strict=True)
    ]
    same = stable[0] == stable[1]
    old_building = np.array([record["kind"] == "building" for record in registries[0]])[
        frames[0]
    ]
    return {
        "method": "Exact global pixel-center feature identity comparison using semantic kind plus genuine gml_id, or explicit artistic representation ID, not release-local numeric codes.",
        "pixels_compared": int(same.size),
        "same_feature_pixel_count": int(same.sum()),
        "old_building_pixel_count": int(old_building.sum()),
        "old_building_same_feature_pixel_count": int((same & old_building).sum()),
        "geographic_fidelity_approved": False,
    }


def overlap_audit(directory, previous_directory):
    import numpy as np
    from PIL import Image
    from render_geometry import relative_asset

    previous_path = previous_directory / "pilot-source.json"
    old = json.loads(previous_path.read_text())
    new = json.loads((directory / INDEX).read_text())
    if old.get("status") != "complete" or old.get("native_resolution_px") != [
        3072,
        3072,
    ]:
        raise ValueError("Overlap audit requires the complete native pilot receipt")
    comparisons = []
    for entry in old["outputs"]:
        prior = relative_asset(previous_directory, entry["path"])
        if digest(prior) != entry["sha256"]:
            raise ValueError("Original pilot frame checksum mismatch")
        with Image.open(prior) as opened:
            before = np.asarray(opened.convert("RGB"), dtype=np.int16)
        with Image.open(directory / entry["path"]) as opened:
            after = np.asarray(
                opened.crop((1024, 1024, 4096, 4096)).convert("RGB"), dtype=np.int16
            )
        difference = np.abs(before - after)
        comparisons.append(
            {
                "id": entry["id"],
                "old_source_sha256": entry["sha256"],
                "new_frame_sha256": digest(directory / entry["path"]),
                "pixels_compared": 3072**2,
                "identical_pixel_count": int(np.all(difference == 0, axis=2).sum()),
                "mean_absolute_channel_difference": float(difference.mean()),
                "maximum_channel_difference": int(difference.max()),
                "pixel_equal": not bool(difference.any()),
            }
        )
    return {
        "previous_receipt_sha256": digest(previous_path),
        "old_global_bounds_px": [0, 0, 3072, 3072],
        "old_area_crop_in_expansion_px": [1024, 1024, 4096, 4096],
        "projection_preserved": True,
        "previous_source_modified": False,
        "previous_artwork_modified": False,
        "comparisons": comparisons,
        "feature_identity_comparison": guide_overlap(
            directory, previous_directory, old, new
        ),
        "interpretation": "Same camera rays and density, not guaranteed identical lighting or source pixels. Expanded scene geometry, terrain extent, vegetation and stochastic rendering may affect the old overlap. This comparison does not approve geographic fidelity.",
    }


def finalize(directory, previous_directory):
    index = json.loads((directory / INDEX).read_text())
    if index.get("native_resolution_px") != [SIZE, SIZE] or index.get(
        "global_bounds_px"
    ) != list(BOUNDS):
        raise ValueError(
            "Expansion source must be exactly the declared native global frame"
        )
    from atlas import parse_config

    config = parse_config(index["config"])
    validate_camera(config)
    if index.get("source_config_sha256") != config.fingerprint:
        raise ValueError("Expansion source config checksum mismatch")
    index = finalize_partition(directory, grid_plan(), index_name=INDEX)
    index["overlap_audit"] = overlap_audit(directory, previous_directory)
    index["planning_hints"] = landmark_plan(config)
    index["frontier_priority"] = ["x3_y2", "x3_y1", "x3_y3"]
    write_index(directory, index, INDEX)
    return index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    rendering = sub.add_parser("render")
    rendering.add_argument(
        "--config", type=Path, default=Path("configs/shinjuku-expansion-source.json")
    )
    rendering.add_argument(
        "--presets", type=Path, default=Path("configs/shinjuku-art-presets.json")
    )
    for name, default in (
        ("manifest", "expansion-buildings-v1/manifest.json"),
        ("ground", "ground-expansion-v1/manifest.json"),
        ("terrain", "terrain-expansion-v1/mesh.json"),
        ("context", "context-expansion-v1/manifest.json"),
        ("vegetation", "vegetation-expansion-v1/manifest.json"),
    ):
        rendering.add_argument("--" + name, type=Path, default=Path("data") / default)
    rendering.set_defaults(guides=True, max_source_tiles=512, max_source_mib=1024)
    rendering.add_argument("--output", type=Path, required=True)
    rendering.add_argument(
        "--public-dataset",
        type=Path,
        default=Path("datasets/shinjuku-expansion-vegetation"),
    )
    finishing = sub.add_parser("finalize")
    finishing.add_argument("--output", type=Path, required=True)
    finishing.add_argument(
        "--previous", type=Path, default=Path("renders/shinjuku-pilot-source-v1")
    )
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else sys.argv[1:]
    args = parser.parse_args(argv)
    if args.command == "render":
        config = load_config(args.config)
        render_native(
            args,
            config,
            validate_camera(config),
            grid_plan(),
            index_name=INDEX,
            extra={
                "kind": "native-source-expansion",
                "global_bounds_px": list(BOUNDS),
                "image_local_origin_global_px": list(BOUNDS[:2]),
                "source_geometry_changed": False,
                "source_selection_changed": True,
                "source_expanded": True,
                "previous_artwork_regenerated": False,
                "source_provenance": source_provenance(
                    args.manifest, args.vegetation, args.public_dataset
                ),
            },
        )
    else:
        finalize(args.output, args.previous)
        print("Finalized 25 native expansion cores with exact source continuity")


if __name__ == "__main__":
    main()

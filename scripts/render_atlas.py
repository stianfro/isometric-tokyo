"""Render bounded, globally aligned atlas chunks using Blender.

Run through Blender, then finalize render-index.json with render_geometry.py in
regular Python. Every chunk sees the same full loaded meshes and terrain. The
renderer does not imply that this limited input contains all city geometry that
could influence a pixel; that remains an explicit source-coverage review gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atlas import (
    camera_for_bounds,
    job_fingerprint,
    load_config,
    pixel_to_world,
    plan_chunks,
)  # noqa: E402
from geo import local_point  # noqa: E402
from render_sample import ground_mesh, import_tiles, render_view, setup_scene  # noqa: E402

LAYERS = ("textured", "geometry")


def arguments():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--ground", type=Path)
    parser.add_argument("--terrain", type=Path)
    parser.add_argument(
        "--reference",
        action="store_true",
        help="Also render one full-view reference for a bounded chunk comparison",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--guides",
        action="store_true",
        help="Export pixel-center source IDs, semantic/silhouette masks and numeric depth/normals",
    )
    parser.add_argument("--max-guide-pixels", type=int, default=1024 * 1024)
    parser.add_argument("--samples", type=int)
    parser.add_argument("--chunk-size", type=int)
    parser.add_argument("--max-chunks", type=int, default=16)
    parser.add_argument("--max-source-tiles", type=int, default=128)
    parser.add_argument("--max-source-mib", type=int, default=512)
    parser.add_argument("--max-frame-pixels", type=int, default=4096 * 4096)
    parser.add_argument(
        "--fixture",
        action="store_true",
        help="Render a low-cost synthetic nonsquare 3x3 seam test, not geographic data",
    )
    args = parser.parse_args(argv)
    if not args.fixture and not args.manifest:
        parser.error("--manifest is required unless --fixture is used")
    if args.samples is not None and not 1 <= args.samples <= 4096:
        parser.error("--samples must be in [1,4096]")
    if args.chunk_size is not None and not 1 <= args.chunk_size <= 4096:
        parser.error("--chunk-size must be in [1,4096]")
    if not 1 <= args.max_chunks <= 4096 or not 1 <= args.max_frame_pixels <= 16384**2:
        parser.error("Invalid render safety limits")
    if not 1 <= args.max_source_tiles <= 4096 or not 1 <= args.max_source_mib <= 16384:
        parser.error("Invalid source safety limits")
    if not 1 <= args.max_guide_pixels <= 16384**2:
        parser.error("Invalid guide ray/pixel budget")
    return args


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clear_scene():
    import bpy

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def geometry_stats(objects):
    """Read global bounds without trimming or modifying any source mesh."""
    import numpy as np

    minima, maxima = [], []
    for obj in objects:
        if not len(obj.data.vertices):
            continue
        coordinates = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
        obj.data.vertices.foreach_get("co", coordinates)
        matrix = np.array(obj.matrix_world, dtype=np.float64)
        world = coordinates.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]
        minima.append(world.min(axis=0))
        maxima.append(world.max(axis=0))
    if not minima:
        raise ValueError("No source building vertices were imported")
    bounds = np.min(minima, axis=0).tolist() + np.max(maxima, axis=0).tolist()
    if not all(math.isfinite(value) for value in bounds):
        raise ValueError("Non-finite building geometry")
    return {
        "mesh_count": len(objects),
        "vertex_count": sum(len(obj.data.vertices) for obj in objects),
        "bounds_enu_m": bounds,
        "source_geometry_cropped": False,
    }


def prepare_source(args, config):
    data = json.loads(args.manifest.read_text())
    if (
        not isinstance(data.get("tiles"), list)
        or not 1 <= len(data["tiles"]) <= args.max_source_tiles
    ):
        raise ValueError("Building input exceeds the source tile-count budget")
    source_paths = [args.manifest.parent / tile["glb_path"] for tile in data["tiles"]]
    if args.guides:
        source_paths += [args.manifest.parent / tile["path"] for tile in data["tiles"]]
    if args.ground:
        ground_meta = json.loads(args.ground.read_text())
        source_paths += [
            args.ground,
            args.ground.parent / ground_meta.get("image", "ground.png"),
        ]
    if args.terrain:
        source_paths.append(args.terrain)
    source_bytes = sum(path.stat().st_size for path in source_paths)
    if source_bytes > args.max_source_mib * 1024 * 1024:
        raise ValueError("Building/ground/terrain files exceed the input byte budget")
    lon, lat = config.origin_lonlat
    objects = import_tiles(args.manifest, data, lon, lat)
    for obj in objects:
        obj["atlas_semantic"] = "building"
    stats = geometry_stats(objects)
    if (
        stats["bounds_enu_m"][2] < config.vertical_range_enu_m[0]
        or stats["bounds_enu_m"][5] > config.vertical_range_enu_m[1]
    ):
        raise ValueError(
            f"Loaded geometry exceeds configured vertical range: {stats['bounds_enu_m']}"
        )
    west, south, east, north = data["bbox"]
    corners = [
        local_point(x, y, 0, lon, lat) for x in (west, east) for y in (south, north)
    ]
    bounds = (
        min(point[0] for point in corners),
        min(point[1] for point in corners),
        max(point[0] for point in corners),
        max(point[1] for point in corners),
    )
    ground_height = stats["bounds_enu_m"][2]
    ground = ground_mesh(
        bounds, ground_height, args.ground, args.terrain, lon, lat, clip=False
    )
    ground.name = "Full loaded terrain, uncropped at image boundaries"
    ground["atlas_semantic"] = "ground"
    hashes = {"building-manifest": sha256(args.manifest)}
    for number, tile in enumerate(data["tiles"]):
        hashes[f"building-glb-{number}"] = sha256(
            args.manifest.parent / tile["glb_path"]
        )
    if args.ground:
        hashes["ground-manifest"] = sha256(args.ground)
        ground_meta = json.loads(args.ground.read_text())
        hashes["ground-image"] = sha256(
            args.ground.parent / ground_meta.get("image", "ground.png")
        )
    if args.terrain:
        hashes["terrain-mesh"] = sha256(args.terrain)
    provenance = {
        "input_sha256": hashes,
        "input_file_bytes": source_bytes,
        "selection_bbox_wsen": data["bbox"],
        "source_attribution": data.get("attribution", ""),
        "source_license": data.get("license", {}),
        "source_date": data.get("source_date", "unspecified"),
        "building_count_in_selected_tiles": data.get("building_count"),
        "lod_counts_in_selected_tiles": data.get("lod_counts", {}),
        "ground_attribution": json.loads(args.ground.read_text()).get("attribution")
        if args.ground
        else None,
        "ground_bounds_wsen": ground_meta["bounds_wsen"] if args.ground else None,
        "terrain_attribution": json.loads(args.terrain.read_text()).get("attribution")
        if args.terrain
        else None,
        "building_feature_ids_preserved": False,
        "terrain_edge_reconciliation_performed": False,
        "ground_uv_mapping": "Geodetic inverse at mesh vertices followed by Web Mercator UV; interpolation inside triangles",
        "limitations": [
            "All loaded meshes are retained, but unselected source tiles may contain missing occluders or shadow casters.",
            "Input selection bounds are not a certificate of visible coverage or source completeness.",
            "Building feature IDs and depth, normal, silhouette and semantic guides are not exported yet.",
            "Photographic shadows, mixed LOD and terrain alignment require source review.",
        ],
    }
    return ground_height, stats, provenance


def material(name, color):
    import bpy

    result = bpy.data.materials.new(name)
    result.use_nodes = True
    shader = result.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*color, 1)
    shader.inputs["Roughness"].default_value = 1
    return result


def fixture_scene(config):
    """Known continuous geometry with tall buildings crossing chunk joins."""
    import bpy

    ground_material = material("Fixture warm ground", (0.62, 0.55, 0.43))
    tower_material = material("Fixture red tower", (0.65, 0.18, 0.11))
    blue_material = material("Fixture blue block", (0.12, 0.30, 0.52))
    bpy.ops.mesh.primitive_plane_add(size=3000, location=(0, 0, 0))
    bpy.context.object.name = "Continuous ground across all nine image chunks"
    bpy.context.object.data.materials.append(ground_material)
    bpy.context.object["atlas_semantic"] = "ground"
    fixtures = [
        ((96, 96), (20, 26, 115), tower_material),
        ((192, 96), (44, 24, 45), blue_material),
        ((144, 192), (50, 18, 28), tower_material),
    ]
    for number, (pixel, dimensions, surface) in enumerate(fixtures):
        # Put a building's base exactly at a four-way pixel join. Its footprint,
        # upper storeys and cast shadow must survive every independently rendered view.
        x, y, _ = pixel_to_world(config, pixel, 0)
        bpy.ops.mesh.primitive_cube_add(size=1, location=(x, y, dimensions[2] / 2))
        cube = bpy.context.object
        cube.name = f"Cross-boundary fixture building {number}"
        cube["atlas_semantic"] = "building"
        cube["atlas_fixture_feature_id"] = number + 2
        cube.dimensions = dimensions
        cube.data.materials.append(surface)
    bpy.context.view_layer.update()
    return (
        0.0,
        {"mesh_count": 4, "source_geometry_cropped": False},
        {
            "input_sha256": {
                "synthetic-fixture": hashlib.sha256(
                    b"crossing-towers-shadow-v1"
                ).hexdigest()
            },
            "source_attribution": "Original synthetic geometry, not geographic source data",
            "limitations": [
                "This fixture checks camera alignment and image-edge preservation, not Tokyo source coverage."
            ],
        },
    )


def set_camera(scene, camera, plan):
    from mathutils import Vector

    target = Vector(plan["target_enu"])
    camera.location = Vector(plan["location_enu"])
    camera.rotation_euler = (
        (target - camera.location).to_track_quat("-Z", "Y").to_euler()
    )
    camera.data.type = "ORTHO"
    camera.data.sensor_fit = plan["sensor_fit"]
    camera.data.ortho_scale = plan["ortho_scale_m"]
    camera.data.clip_start = 0.1
    camera.data.clip_end = 100000
    scene.render.resolution_x, scene.render.resolution_y = plan["resolution_px"]
    scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
    scene.render.resolution_percentage = 100


def render_frame(scene, camera, plan, args, geometry_material):
    set_camera(scene, camera, plan)
    started = time.monotonic()
    plan["frames"] = {}
    for layer in LAYERS:
        scene.view_layers[0].material_override = (
            geometry_material if layer == "geometry" else None
        )
        path = Path("overscan") / layer / f"{plan['id']}.png"
        destination = args.output / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        render_view(scene, destination)
        plan["frames"][layer] = path.as_posix()
    scene.view_layers[0].material_override = None
    plan["render_seconds"] = round(time.monotonic() - started, 3)
    return plan


def write_index(path, index):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(index, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def main():
    import bpy

    args = arguments()
    config = load_config(args.config)
    if args.fixture:
        config = replace(
            config,
            release_id="synthetic-seam-v1",
            camera_target_enu=(0.0, 0.0, 20.0),
            camera_offset_enu=(1500.0, -1500.0, 1500.0),
            pixel_origin=(144.0, 108.0),
            pixels_per_metre=1.0,
            bounds_px=(0, 0, 288, 216),
            chunk_size_px=96,
            overscan_px=8,
            render_samples=128,
            vertical_range_enu_m=(0.0, 180.0),
        )
    if args.samples is not None:
        config = replace(config, render_samples=args.samples)
    if args.chunk_size is not None:
        config = replace(config, chunk_size_px=args.chunk_size)
    plan = plan_chunks(config, max_chunks=args.max_chunks)
    for chunk in plan["chunks"]:
        if (
            math.prod(chunk["resolution_px"]) > args.max_frame_pixels
            or max(chunk["resolution_px"]) > 16384
        ):
            raise ValueError("Frame exceeds explicit pixel budget or maximum dimension")
    args.output.mkdir(parents=True, exist_ok=True)
    index_path = args.output / "render-index.json"
    if index_path.exists():
        raise ValueError(
            "Output already contains a render index; choose a fresh directory"
        )
    clear_scene()
    ground_height, stats, provenance = (
        fixture_scene(config) if args.fixture else prepare_source(args, config)
    )
    guides = None
    guide_description = None
    if args.guides:
        from render_guides import (
            build_guide_scene,
            guide_metadata,
            render_guides,
            source_registry,
        )

        core_plans = plan["chunks"] + (
            [{"bounds_px": config.bounds_px}] if args.fixture or args.reference else []
        )
        if any(
            (item["bounds_px"][2] - item["bounds_px"][0])
            * (item["bounds_px"][3] - item["bounds_px"][1])
            > args.max_guide_pixels
            for item in core_plans
        ):
            raise ValueError("Guide frame exceeds explicit ray/pixel budget")
        if args.fixture:
            registry = {
                "schema_version": 1,
                "identity_scope": "Original synthetic fixture objects, not source building IDs",
                "records": [
                    {"id": 0, "kind": "background"},
                    {"id": 1, "kind": "ground"},
                ]
                + [
                    {"id": number + 2, "kind": "synthetic-object"}
                    for number in range(3)
                ],
                "tile_batch_feature_ids": {},
                "unresolved_tile_feature_ids": {},
            }
        else:
            registry, source_hashes = source_registry(args.manifest)
            provenance["input_sha256"].update(source_hashes)
        guides = build_guide_scene(list(bpy.context.scene.objects), config, registry)
        registry_relative = "guides/feature-registry.json"
        registry_path = args.output / registry_relative
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(
            json.dumps(registry, indent=2, ensure_ascii=False) + "\n"
        )
        guide_description = guide_metadata(
            guides, registry, registry_relative, sha256(registry_path)
        )
        provenance["building_feature_ids_preserved"] = (
            not args.fixture and guides["unresolved_building_meshes"] == 0
        )
        if not args.fixture:
            provenance["limitations"] = [
                item
                for item in provenance["limitations"]
                if "guides are not exported" not in item
            ]
            provenance["limitations"].append(
                "Guides sample opaque geometric surfaces; textured transparency and interpolated shading normals are not used."
            )
    settings = argparse.Namespace(samples=config.render_samples, resolution=128)
    scene, camera = setup_scene(settings, ground_height)
    scene.cycles.seed = config.render_seed
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 4
    scene.render.use_stamp = False
    if args.fixture:
        # Remove stochastic environment lighting to isolate projection errors.
        # A directional sun still produces shadows crossing the chunk boundaries.
        scene.world.node_tree.nodes["Background"].inputs[1].default_value = 0
        scene.cycles.max_bounces = 1
        for obj in scene.objects:
            if obj.type == "LIGHT":
                obj.data.angle = 0
    geometry_material = material(
        "Atlas geometry diagnostic override", (0.7, 0.72, 0.73)
    )
    versions = {
        "blender": bpy.app.version_string,
        "renderer": "atlas-source-v1",
        "renderer-script": sha256(Path(__file__))[:16],
    }
    for dependency in ("atlas.py", "geo.py", "gltf_compat.py", "render_sample.py"):
        versions["script-" + dependency.replace("_", "-").replace(".", "-")] = sha256(
            Path(__file__).parent / dependency
        )
    if args.guides:
        for dependency in ("render_guides.py", "fetch_sample.py"):
            versions["script-" + dependency.replace("_", "-").replace(".", "-")] = (
                sha256(Path(__file__).parent / dependency)
            )
    index = {
        **plan,
        "status": "rendering",
        "config": config.to_dict(),
        "layers": list(LAYERS),
        "chunks": [],
        "fixture": "crossing-towers-shadow-v1" if args.fixture else None,
        "geometry": stats,
        "provenance": provenance,
        "tool_versions": versions,
        "source_coverage_verified": False,
        "ai_styling": False,
        "guide_metadata": guide_description,
        "render_settings": {
            "engine": "CYCLES",
            "device": "CPU",
            "samples": config.render_samples,
            "seed": config.render_seed,
            "adaptive_sampling": False,
            "denoising": False,
            "colour_transform": "Standard",
            "look": "None",
            "exposure": 0,
            "gamma": 1,
        },
    }
    write_index(index_path, index)
    for chunk in plan["chunks"]:
        chunk["job_sha256"] = job_fingerprint(
            config, chunk["bounds_px"], provenance["input_sha256"], versions
        )
        index["chunks"].append(
            render_frame(scene, camera, chunk, args, geometry_material)
        )
        if guides is not None:
            render_guides(
                config, chunk, guides, args.output, max_pixels=args.max_guide_pixels
            )
        write_index(index_path, index)
    if args.fixture or args.reference:
        reference = {
            "id": "reference",
            **camera_for_bounds(config, config.bounds_px, config.overscan_px),
        }
        if math.prod(reference["resolution_px"]) > args.max_frame_pixels:
            raise ValueError("Reference frame exceeds explicit pixel budget")
        index["reference"] = render_frame(
            scene, camera, reference, args, geometry_material
        )
        if guides is not None:
            render_guides(
                config, reference, guides, args.output, max_pixels=args.max_guide_pixels
            )
    index["status"] = "rendered-overscan"
    write_index(index_path, index)
    print(
        "Rendered source chunks. Finalize image-space crops with render_geometry.py.",
        flush=True,
    )


if __name__ == "__main__":
    main()

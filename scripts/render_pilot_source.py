"""Native monolithic source rendering and lossless 3 by 3 pilot crops.

Rendering runs in Blender. Finalization runs in ordinary Python with Pillow.
The nine cores partition one native render, not an enlarged illustration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import sys
import time
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atlas import camera_for_bounds, load_config  # noqa: E402

INDEX = "pilot-source.json"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_index(directory, value):
    temporary = directory / (INDEX + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(directory / INDEX)


def strip_png_metadata(path):
    """Keep image/color chunks byte-identical, remove ancillary text and EXIF."""
    payload = path.read_bytes()
    if not payload.startswith(PNG_SIGNATURE):
        raise ValueError("Expected a PNG render")
    offset, kept, ended = 8, [PNG_SIGNATURE], False
    while offset < len(payload):
        if offset + 12 > len(payload):
            raise ValueError("Truncated PNG chunk")
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        stop = offset + length + 12
        if stop > len(payload):
            raise ValueError("Truncated PNG payload")
        kind = payload[offset + 4 : offset + 8]
        body = payload[offset + 4 : stop - 4]
        checksum = struct.unpack(">I", payload[stop - 4 : stop])[0]
        if zlib.crc32(body) & 0xFFFFFFFF != checksum:
            raise ValueError("PNG checksum mismatch")
        if kind not in (b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"):
            kept.append(payload[offset:stop])
        offset = stop
        if kind == b"IEND":
            ended = True
            break
    if not ended or offset != len(payload):
        raise ValueError("PNG must end at IEND")
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(b"".join(kept))
    temporary.replace(path)


def grid_plan(size, core, context=256):
    if (
        type(size) is not int
        or type(core) is not int
        or type(context) is not int
        or not 1 <= core <= 1024
        or size != core * 3
        or not 0 <= context <= core
    ):
        raise ValueError("Pilot requires a 3 by 3 grid, cores at most 1024 pixels")
    chunks = []
    for y in range(3):
        for x in range(3):
            left, top = x * core, y * core
            bounds = [left, top, left + core, top + core]
            expanded = [
                max(0, left - context),
                max(0, top - context),
                min(size, left + core + context),
                min(size, top + core + context),
            ]
            chunks.append(
                {
                    "id": f"x{x}_y{y}",
                    "bounds_px": bounds,
                    "context_bounds_px": expanded,
                    "core_offset_in_context_px": [
                        left - expanded[0],
                        top - expanded[1],
                    ],
                }
            )
    return chunks


def validate_camera(config):
    if (
        list(config.bounds_px) != [0, 0, 3072, 3072]
        or config.chunk_size_px != 1024
        or config.overscan_px != 0
        or config.render_samples != 64
        or not math.isclose(3072 / config.pixels_per_metre, 1260, abs_tol=1e-8)
    ):
        raise ValueError(
            "Native pilot camera must be 3072 pixels over 1260 metres, 64 samples"
        )
    return camera_for_bounds(config, config.bounds_px)


def apply_ink(scene, preset):
    from render_atlas import material
    from render_style_pilot import linear_color, palette_for, style_material

    building = style_material(preset)
    ground = material("Pilot neutral terrain", linear_color(preset["ground"]))
    surfaces = {
        kind: material("Pilot " + kind, linear_color(color))
        for kind, color in palette_for(preset).items()
    }
    for obj in scene.objects:
        if obj.type == "LIGHT" and obj.data.type == "SUN":
            obj.data.energy = preset["sun_energy"]
            obj.data.angle = math.radians(preset["sun_angle_degrees"])
        if obj.type != "MESH":
            continue
        semantic = obj.get("atlas_semantic")
        selected = ground
        if semantic == "building":
            selected = building
        elif semantic in ("transportation", "water"):
            selected = surfaces["road" if semantic == "transportation" else "water"]
        elif semantic == "vegetation":
            part = obj.get("vegetation_part", "canopy")
            selected = surfaces[
                {"trunk": "bark", "cover": "groundcover"}.get(part, "vegetation")
            ]
        obj.data.materials.clear()
        obj.data.materials.append(selected)
        for polygon in obj.data.polygons:
            polygon.material_index = 0
    scene.world.node_tree.nodes["Background"].inputs[1].default_value = preset[
        "world_strength"
    ]


def render(args):
    import bpy
    from render_atlas import clear_scene, prepare_source, set_camera
    from render_sample import render_view, setup_scene
    from render_style_pilot import display_credits, parse_presets

    config = load_config(args.config)
    camera_plan = validate_camera(config)
    preset = next(
        preset
        for preset in parse_presets(json.loads(args.presets.read_text()))["presets"]
        if preset["id"] == "atlas-ink"
    )
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("Pilot source requires an empty output directory")
    args.output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    clear_scene()
    ground_height, stats, provenance = prepare_source(args, config)
    scene, camera = setup_scene(
        argparse.Namespace(samples=64, resolution=3072), ground_height
    )
    set_camera(scene, camera, camera_plan)
    scene.cycles.seed = config.render_seed
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 4
    scene.render.use_stamp = False
    scene.render.image_settings.color_depth = "8"
    index = {
        "schema_version": 1,
        "status": "rendering",
        "native_resolution_px": [3072, 3072],
        "upscaled": False,
        "ai_styling": False,
        "geographic_fidelity": "not-verified",
        "source_geometry_changed": False,
        "source_coverage_verified": False,
        "config": config.to_dict(),
        "source_config_sha256": config.fingerprint,
        "camera": camera_plan,
        "render_settings": {
            "samples": 64,
            "threads": 4,
            "seed": config.render_seed,
            "engine": "Cycles CPU",
            "denoising": False,
            "adaptive_sampling": False,
            "view_transform": "Standard",
            "blender": bpy.app.version_string,
        },
        "geometry": stats,
        "provenance": provenance,
        "preset": preset,
        "outputs": [],
        "chunks": grid_plan(3072, 1024),
        "limitations": [
            "One bounded Shinjuku camera view, not complete Tokyo coverage.",
            "All cores are lossless crops of the same native source render and camera.",
            "Procedural facade marks, vegetation dimensions and woodland fill are artistic, not surveyed details.",
            "Palette studies do not approve geographic fidelity or AI art registration.",
        ],
    }
    index["display_attribution"] = display_credits(index)
    write_index(args.output, index)
    for identifier in ("source-reference", "atlas-ink"):
        if identifier == "atlas-ink":
            apply_ink(scene, preset)
        frame_started = time.monotonic()
        path = args.output / (identifier + ".png")
        render_view(scene, path)
        strip_png_metadata(path)
        index["outputs"].append(
            {
                "id": identifier,
                "path": path.name,
                "sha256": digest(path),
                "width": 3072,
                "height": 3072,
                "bytes": path.stat().st_size,
                "render_seconds": round(time.monotonic() - frame_started, 3),
            }
        )
        write_index(args.output, index)
        print("PILOT_FRAME_READY " + path.name, flush=True)
    if args.guides:
        from render_guides import (
            build_guide_scene,
            guide_metadata,
            render_guides,
            source_registry,
        )
        from render_vegetation import register_vegetation_objects

        registry, source_hashes = source_registry(
            args.manifest, context_path=args.context
        )
        provenance["input_sha256"].update(source_hashes)
        register_vegetation_objects(registry, list(scene.objects))
        guide_scene = build_guide_scene(list(scene.objects), config, registry)
        registry_path = args.output / "guides/feature-registry.json"
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(
            json.dumps(registry, indent=2, ensure_ascii=False) + "\n"
        )
        index["guide_metadata"] = guide_metadata(
            guide_scene, registry, "guides/feature-registry.json", digest(registry_path)
        )
        guide_started = time.monotonic()
        index["guide_assets"] = render_guides(
            config,
            {"id": "reference", "bounds_px": config.bounds_px},
            guide_scene,
            args.output,
            max_pixels=3072**2,
        )
        index["guide_seconds"] = round(time.monotonic() - guide_started, 3)
        provenance["building_feature_ids_preserved"] = (
            guide_scene["unresolved_building_meshes"] == 0
        )
        provenance["limitations"] = [
            value
            for value in provenance["limitations"]
            if "guides are not exported" not in value
        ]
    index["status"] = "rendered"
    index["total_render_seconds"] = round(time.monotonic() - started, 3)
    write_index(args.output, index)


def finalize(directory):
    from PIL import Image
    from render_geometry import relative_asset

    index = json.loads((directory / INDEX).read_text())
    if (
        index.get("status") not in ("rendered", "complete")
        or index.get("upscaled") is not False
    ):
        raise ValueError("Expected a completed native render")
    width, height = index["native_resolution_px"]
    if width != height:
        raise ValueError("Pilot source must be square")
    expected = grid_plan(width, width // 3, min(256, width // 3))
    for actual, planned in zip(index["chunks"], expected, strict=True):
        if any(actual.get(key) != value for key, value in planned.items()):
            raise ValueError("Pilot partition was changed")
    outputs = index["outputs"]
    if [item["id"] for item in outputs] != ["source-reference", "atlas-ink"]:
        raise ValueError("Both source and Atlas ink frames are required")
    for entry in outputs:
        path = relative_asset(directory, entry["path"])
        if digest(path) != entry["sha256"]:
            raise ValueError("Source image checksum mismatch")
        with Image.open(path) as opened:
            if opened.size != (width, height) or opened.mode != "RGB":
                raise ValueError("Native render must be RGB at its declared dimensions")
            original = opened.copy()
        assembled = Image.new("RGB", original.size)
        for chunk in index["chunks"]:
            chunk.setdefault("assets", {})
            assets = {}
            for kind, bounds in (
                ("core", chunk["bounds_px"]),
                ("context", chunk["context_bounds_px"]),
            ):
                relative = f"{kind}/{entry['id']}/{chunk['id']}.png"
                target = directory / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                crop = original.crop(bounds)
                crop.save(target)
                assets[kind] = {
                    "path": relative,
                    "sha256": digest(target),
                    "width": crop.width,
                    "height": crop.height,
                }
                if kind == "core":
                    assembled.paste(crop, tuple(bounds[:2]))
            chunk["assets"][entry["id"]] = assets
        if assembled.tobytes() != original.tobytes():
            raise ValueError("Lossless core reassembly does not match native render")
    # Guides retain categorical and float encodings, with no resampling.
    for name, entry in index.get("guide_assets", {}).items():
        if name not in {
            "feature-id",
            "semantic",
            "silhouette",
            "normal-preview",
            "depth-preview",
            "depth",
            "normal",
        }:
            raise ValueError("Unsupported guide name")
        path = relative_asset(directory, entry["path"])
        if digest(path) != entry["sha256"] or (entry["width"], entry["height"]) != (
            width,
            height,
        ):
            raise ValueError("Guide checksum or dimensions mismatch")
        if path.suffix == ".png":
            with Image.open(path) as guide:
                if guide.size != (width, height):
                    raise ValueError("Guide dimensions mismatch")
                if name == "semantic":
                    index["guide_semantic_pixel_counts"] = {
                        str(category): count
                        for category, count in enumerate(guide.histogram())
                        if count
                    }
                for chunk in index["chunks"]:
                    target = directory / f"guides/{name}/{chunk['id']}.png"
                    guide.crop(chunk["bounds_px"]).save(target)
                    chunk.setdefault("guides", {})[name] = {
                        "path": target.relative_to(directory).as_posix(),
                        "sha256": digest(target),
                    }
        elif path.suffix == ".f32":
            data = path.read_bytes()
            components = entry["components"]
            if (
                components != (3 if name == "normal" else 1)
                or len(data) != width * height * components * 4
            ):
                raise ValueError("Invalid float guide length")
            stride = width * components * 4
            for chunk in index["chunks"]:
                left, top, right, bottom = chunk["bounds_px"]
                crop = b"".join(
                    data[
                        row * stride + left * components * 4 : row * stride
                        + right * components * 4
                    ]
                    for row in range(top, bottom)
                )
                target = directory / f"guides/{name}/{chunk['id']}.f32"
                target.write_bytes(crop)
                chunk.setdefault("guides", {})[name] = {
                    "path": target.relative_to(directory).as_posix(),
                    "sha256": digest(target),
                }
        else:
            raise ValueError("Unsupported guide asset")
    index["status"] = "complete"
    index["core_reassembly_pixel_exact"] = True
    index["context_rule"] = (
        "256 pixel context clipped at native image edges, no invented padding"
    )
    write_index(directory, index)
    return index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    rendering = commands.add_parser("render")
    rendering.add_argument(
        "--config", type=Path, default=Path("configs/shinjuku-pilot-source.json")
    )
    rendering.add_argument(
        "--presets", type=Path, default=Path("configs/shinjuku-art-presets.json")
    )
    for name, default in (
        ("manifest", "coverage-buildings/manifest.json"),
        ("ground", "ground-study-v2/manifest.json"),
        ("terrain", "terrain-study-v2/mesh.json"),
        ("context", "context-study-v2/manifest.json"),
        ("vegetation", "vegetation/manifest.json"),
    ):
        rendering.add_argument("--" + name, type=Path, default=Path("data") / default)
    rendering.set_defaults(guides=True, max_source_tiles=512, max_source_mib=1024)
    rendering.add_argument("--output", type=Path, required=True)
    finishing = commands.add_parser("finalize")
    finishing.add_argument("--output", type=Path, required=True)
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else sys.argv[1:]
    args = parser.parse_args(argv)
    if args.command == "render":
        render(args)
    else:
        finalize(args.output)
        print("Finalized nine native pilot cores with exact source continuity")


if __name__ == "__main__":
    main()

"""Bounded deterministic material studies from unchanged PLATEAU geometry.

Blender runs the render command. Ordinary Python runs finalize, strips image
metadata and creates a labeled comparison sheet. No external image model is
used, and none of these structural studies is an approved map style.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atlas import camera_for_bounds, load_config  # noqa: E402

HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
PRESET_KEYS = {
    "id",
    "title",
    "description",
    "facade",
    "roof",
    "ground",
    "roof_normal_z_min",
    "sun_energy",
    "sun_angle_degrees",
    "world_strength",
}

OPTIONAL_PALETTE = {"window", "road", "water", "vegetation", "bark", "groundcover"}
OPTIONAL_NUMBERS = {"window_spacing_m": (1.5, 12.0), "floor_height_m": (2.5, 6.0)}


def palette_for(preset):
    """Plain original palettes remain supported; context colors are explicit."""
    return {
        "road": preset.get("road", preset["ground"]),
        "water": preset.get("water", "#9cbec5"),
        "vegetation": preset.get("vegetation", "#718f68"),
        "bark": preset.get("bark", "#665a45"),
        "groundcover": preset.get("groundcover", "#b3bc8c"),
    }


def linear_color(value):
    if not isinstance(value, str) or not HEX_COLOR.fullmatch(value):
        raise ValueError("Palette colors must be #RRGGBB strings")
    channels = [int(value[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    return tuple(
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    )


def parse_presets(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "status", "presets"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
    ):
        raise ValueError("Unsupported style-preset schema")
    if value["status"] != "unapproved-structural-studies":
        raise ValueError("This pilot cannot mark an art direction approved")
    presets = value["presets"]
    if not isinstance(presets, list) or not 1 <= len(presets) <= 3:
        raise ValueError("The bounded pilot accepts one to three candidates")
    identifiers = set()
    limits = {
        "roof_normal_z_min": (0.1, 0.99),
        "sun_energy": (0.1, 3.0),
        "sun_angle_degrees": (0, 20),
        "world_strength": (0.1, 2.0),
    }
    for preset in presets:
        if (
            not isinstance(preset, dict)
            or not PRESET_KEYS <= set(preset)
            or set(preset) - PRESET_KEYS - OPTIONAL_PALETTE - OPTIONAL_NUMBERS.keys()
        ):
            raise ValueError("Style preset has missing or unknown fields")
        identifier = preset["id"]
        if (
            not isinstance(identifier, str)
            or not IDENTIFIER.fullmatch(identifier)
            or identifier in identifiers
        ):
            raise ValueError(
                "Style candidate IDs must be distinct portable identifiers"
            )
        identifiers.add(identifier)
        for field in ("title", "description"):
            if (
                not isinstance(preset[field], str)
                or not 1 <= len(preset[field]) <= 240
                or any(ord(char) < 32 for char in preset[field])
            ):
                raise ValueError("Style labels must be short printable text")
        for field in ("facade", "roof", "ground"):
            linear_color(preset[field])
        for field in OPTIONAL_PALETTE & preset.keys():
            linear_color(preset[field])
        for field, (low, high) in (limits | OPTIONAL_NUMBERS).items():
            if field not in preset:
                continue
            number = preset[field]
            if (
                type(number) not in (int, float)
                or not math.isfinite(number)
                or not low <= number <= high
            ):
                raise ValueError(f"Style setting {field} is outside the pilot range")
    return json.loads(json.dumps(value))


def output_size(bounds, resolution):
    if type(resolution) is not int or not 64 <= resolution <= 1024:
        raise ValueError("Pilot longest side must be between 64 and 1024 pixels")
    width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    scale = resolution / max(width, height)
    result = max(1, round(width * scale)), max(1, round(height * scale))
    if min(result) < 16:
        raise ValueError("Pilot view aspect ratio is too narrow")
    return result


def style_material(preset):
    import bpy

    surface = bpy.data.materials.new("Structural study " + preset["id"])
    surface.use_nodes = True
    nodes, links = surface.node_tree.nodes, surface.node_tree.links
    shader = nodes.get("Principled BSDF")
    shader.inputs["Roughness"].default_value = 1
    geometry = nodes.new("ShaderNodeNewGeometry")
    dot = nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    dot.inputs[1].default_value = (0, 0, 1)
    links.new(geometry.outputs["Normal"], dot.inputs[0])
    roof = nodes.new("ShaderNodeMath")
    roof.operation = "GREATER_THAN"
    roof.inputs[1].default_value = preset["roof_normal_z_min"]
    links.new(dot.outputs["Value"], roof.inputs[0])
    facade_color = None
    if "window" in preset:
        # Procedural facade marks are artistic, not measured source windows.
        position = nodes.new("ShaderNodeSeparateXYZ")
        links.new(geometry.outputs["Position"], position.inputs[0])
        normal = nodes.new("ShaderNodeSeparateXYZ")
        links.new(geometry.outputs["Normal"], normal.inputs[0])

        def scalar(operation, a, b=0):
            node = nodes.new("ShaderNodeMath")
            node.operation = operation
            for index, value in enumerate((a, b)):
                if isinstance(value, (int, float)):
                    node.inputs[index].default_value = value
                else:
                    links.new(value, node.inputs[index])
            return node.outputs[0]

        nx = scalar("ABSOLUTE", normal.outputs["X"])
        ny = scalar("ABSOLUTE", normal.outputs["Y"])
        side = scalar("GREATER_THAN", nx, ny)
        horizontal = scalar(
            "ADD",
            scalar("MULTIPLY", side, position.outputs["Y"]),
            scalar("MULTIPLY", scalar("SUBTRACT", 1, side), position.outputs["X"]),
        )

        def band(coordinate, spacing, start, end):
            fraction = scalar("FRACT", scalar("DIVIDE", coordinate, spacing))
            return scalar(
                "MULTIPLY",
                scalar("GREATER_THAN", fraction, start),
                scalar("LESS_THAN", fraction, end),
            )

        windows = scalar(
            "MULTIPLY",
            band(horizontal, preset.get("window_spacing_m", 3.6), 0.18, 0.80),
            band(position.outputs["Z"], preset.get("floor_height_m", 3.6), 0.24, 0.77),
        )
        glass = nodes.new("ShaderNodeMixRGB")
        glass.inputs[1].default_value = (*linear_color(preset["facade"]), 1)
        glass.inputs[2].default_value = (*linear_color(preset["window"]), 1)
        links.new(windows, glass.inputs[0])
        facade_color = glass.outputs["Color"]
    palette = nodes.new("ShaderNodeMixRGB")
    palette.inputs[1].default_value = (*linear_color(preset["facade"]), 1)
    if facade_color:
        links.new(facade_color, palette.inputs[1])
    palette.inputs[2].default_value = (*linear_color(preset["roof"]), 1)
    links.new(roof.outputs["Value"], palette.inputs[0])
    links.new(palette.outputs["Color"], shader.inputs["Base Color"])
    return surface


def render(args):
    import bpy
    from render_atlas import clear_scene, material, prepare_source, set_camera
    from render_sample import render_view, setup_scene

    config = load_config(args.config)
    presets = parse_presets(json.loads(args.presets.read_text()))
    width, height = output_size(config.bounds_px, args.resolution)
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("Style pilot requires an empty output directory")
    args.output.mkdir(parents=True, exist_ok=True)
    clear_scene()
    ground_height, stats, provenance = prepare_source(args, config)
    scene, camera = setup_scene(
        argparse.Namespace(samples=args.samples, resolution=width), ground_height
    )
    plan = camera_for_bounds(config, config.bounds_px)
    set_camera(scene, camera, plan)
    scene.render.resolution_x, scene.render.resolution_y = width, height
    scene.cycles.seed = config.render_seed
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 4
    scene.render.use_stamp = False
    outputs = []
    render_view(scene, args.output / "source-reference.png")
    outputs.append(
        {
            "id": "source-reference",
            "title": "Source and mapped context"
            if getattr(args, "vegetation", None)
            else "Unstyled source reference",
            "path": "source-reference.png",
        }
    )
    for preset in presets["presets"]:
        building = style_material(preset)
        ground = material(
            "Neutral terrain " + preset["id"], linear_color(preset["ground"])
        )
        palette = palette_for(preset)
        context_materials = {
            kind: material(f"{preset['id']} {kind}", linear_color(color))
            for kind, color in palette.items()
        }
        for obj in scene.objects:
            if obj.type != "MESH":
                continue
            semantic = obj.get("atlas_semantic")
            selected = ground
            if semantic == "building":
                selected = building
            elif semantic == "transportation":
                selected = context_materials["road"]
            elif semantic == "water":
                selected = context_materials["water"]
            elif semantic == "vegetation":
                part = obj.get("vegetation_part", "canopy")
                selected = context_materials[
                    {"trunk": "bark", "cover": "groundcover"}.get(part, "vegetation")
                ]
            obj.data.materials.clear()
            obj.data.materials.append(selected)
            for polygon in obj.data.polygons:
                polygon.material_index = 0
        for obj in scene.objects:
            if obj.type == "LIGHT" and obj.data.type == "SUN":
                obj.data.energy = preset["sun_energy"]
                obj.data.angle = math.radians(preset["sun_angle_degrees"])
        scene.world.node_tree.nodes["Background"].inputs[1].default_value = preset[
            "world_strength"
        ]
        path = preset["id"] + ".png"
        render_view(scene, args.output / path)
        outputs.append({"id": preset["id"], "title": preset["title"], "path": path})
    metadata = {
        "schema_version": 1,
        "status": "rendered-unapproved",
        "art_approval": "pending-owner-review",
        "ai_styling": False,
        "external_generation_cost": 0,
        "source_geometry_changed": False,
        "procedural_facade_details": any(
            "window" in preset for preset in presets["presets"]
        ),
        "mapped_vegetation_representation": getattr(args, "vegetation", None)
        is not None,
        "source_config_sha256": config.fingerprint,
        "source_atlas_bounds_px": list(config.bounds_px),
        "output_resolution_px": [width, height],
        "camera": plan,
        "samples": args.samples,
        "seed": config.render_seed,
        "blender_version": bpy.app.version_string,
        "render_engine": "Cycles CPU, Standard color management, no denoising, no adaptive sampling",
        "presets": presets,
        "geometry": stats,
        "provenance": provenance,
        "outputs": outputs,
        "limitations": [
            "Unapproved structural art-direction studies, not finished illustrations.",
            "All candidates use exactly the same loaded source meshes and camera as the new unstyled reference.",
            "Ground photography is replaced with a palette. Context appears only where supplied; transportation is not subdivided into unverified rail/road types.",
            "Roof/facade classification is a deterministic shader rule on shading-normal Z, not source semantic roof labels.",
            "Procedural window marks and palette choices are artistic treatments, not measured facade details.",
            "Mapped vegetation may use interpreted canopy shapes; source attribution and representation limits are retained separately.",
            "No final art direction, terrain alignment or real-world completeness is approved by this pilot.",
        ],
    }
    (args.output / "style-pilot.json").write_text(json.dumps(metadata, indent=2) + "\n")


def display_credits(metadata):
    """Credit only supplied inputs, never infer the provider from a CLI flag."""
    provenance = metadata.get("provenance", {})
    credits = []
    for label, value in (
        ("City model", provenance.get("source_attribution")),
        ("Imagery", provenance.get("ground_attribution")),
        ("Terrain", provenance.get("terrain_attribution")),
        ("Context", provenance.get("context", {}).get("attribution")),
        ("Vegetation", provenance.get("vegetation", {}).get("source_attribution")),
    ):
        if isinstance(value, str) and value.strip():
            credits.append(f"{label}: {' '.join(value.split())}")
    credits.append(
        "Modified independent study. No endorsement. Artistic details are not measured source facts."
    )
    return credits


def finalize(directory: Path):
    from PIL import Image, ImageDraw, ImageFont, ImageStat
    from render_geometry import file_hash, relative_asset

    path = directory / "style-pilot.json"
    metadata = json.loads(path.read_text())
    if (
        metadata.get("schema_version") != 1
        or metadata.get("status") not in ("rendered-unapproved", "complete-unapproved")
        or metadata.get("art_approval") != "pending-owner-review"
    ):
        raise ValueError("Expected an unapproved style-pilot render")
    size = tuple(metadata["output_resolution_px"])
    images = []
    for entry in metadata["outputs"]:
        target = relative_asset(directory, entry["path"])
        with Image.open(target) as source:
            source.load()
            if (
                source.size != size
                or max(ImageStat.Stat(source.convert("RGB")).stddev) < 5
            ):
                raise ValueError("Style image dimensions or content failed validation")
            clean = Image.new("RGB", size)
            clean.paste(source.convert("RGB"))
            temporary = target.with_suffix(".tmp")
            clean.save(temporary, format="PNG")
            temporary.replace(target)
        entry.update(sha256=file_hash(target), width=size[0], height=size[1])
        images.append((entry["title"], clean))
    padding, label = 20, 48
    credits = display_credits(metadata)
    footer = len(credits) * 24 + padding
    columns, rows = 2, math.ceil(len(images) / 2)
    sheet = Image.new(
        "RGB",
        (
            columns * size[0] + (columns + 1) * padding,
            rows * (size[1] + label) + (rows + 1) * padding + 44 + footer,
        ),
        "#f5f3ed",
    )
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=21)
    small = ImageFont.load_default(size=16)
    draw.text(
        (padding, padding),
        "Tokyo material studies: unapproved, no AI",
        fill="#26353b",
        font=font,
    )
    for number, (title, image) in enumerate(images):
        column, row = number % columns, number // columns
        x, y = (
            padding + column * (size[0] + padding),
            44 + padding + row * (size[1] + label + padding),
        )
        draw.text((x, y), title, fill="#26353b", font=font)
        draw.text(
            (x, y + 25),
            "Fixed camera; source geometry plus declared treatments",
            fill="#59656a",
            font=small,
        )
        sheet.paste(image, (x, y + label))
    for number, credit in enumerate(credits):
        # The bundled bitmap font lacks Japanese glyphs. Full attribution remains
        # in the manifest and adjacent publication captions.
        credit = credit.replace("国土地理院", "GSI Japan")
        credit_font = small
        if draw.textlength(credit, font=credit_font) > sheet.width - 2 * padding:
            credit_font = ImageFont.load_default(
                size=max(
                    4,
                    int(
                        16
                        * (sheet.width - 2 * padding)
                        / draw.textlength(credit, font=small)
                    ),
                )
            )
        draw.text(
            (padding, sheet.height - footer + number * 24),
            credit,
            fill="#59656a",
            font=credit_font,
        )
    sheet_path = directory / "style-comparison.png"
    sheet.save(sheet_path)
    metadata["comparison"] = {
        "path": sheet_path.name,
        "sha256": file_hash(sheet_path),
        "width": sheet.width,
        "height": sheet.height,
    }
    metadata["status"] = "complete-unapproved"
    metadata["display_attribution"] = credits
    path.write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def main():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else sys.argv[1:]
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    rendering = subparsers.add_parser("render")
    rendering.add_argument("--config", type=Path, default=Path("configs/shinjuku.json"))
    rendering.add_argument(
        "--presets", type=Path, default=Path("configs/style-presets.json")
    )
    rendering.add_argument("--manifest", type=Path, required=True)
    rendering.add_argument("--ground", type=Path)
    rendering.add_argument("--terrain", type=Path)
    rendering.add_argument("--context", type=Path)
    rendering.add_argument("--vegetation", type=Path)
    rendering.add_argument("--output", type=Path, required=True)
    rendering.add_argument("--resolution", type=int, default=720)
    rendering.add_argument("--samples", type=int, default=64)
    rendering.add_argument("--max-source-tiles", type=int, default=128)
    rendering.add_argument("--max-source-mib", type=int, default=512)
    rendering.set_defaults(guides=False)
    finishing = subparsers.add_parser("finalize")
    finishing.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "render":
        if (
            not 1 <= args.samples <= 256
            or not 1 <= args.max_source_tiles <= 512
            or not 1 <= args.max_source_mib <= 512
        ):
            parser.error("Pilot sample/source limits exceed the bounded study")
        render(args)
    else:
        result = finalize(args.output)
        print(f"Finalized {len(result['outputs'])} unapproved material-study images")


if __name__ == "__main__":
    main()

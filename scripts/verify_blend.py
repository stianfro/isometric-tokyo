"""Reopen the saved Blender scene and verify its portable assets and camera."""

import argparse
import json
import sys
from pathlib import Path


def main():
    import bpy

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    scene = bpy.context.scene
    if not scene.camera or scene.camera.data.type != "ORTHO":
        raise ValueError("Expected saved orthographic overview camera")
    if scene.view_layers[0].material_override is not None:
        raise ValueError("Expected saved textured view, not diagnostic override")
    textures = [image for image in bpy.data.images if image.source == "FILE"]
    missing = [image.name for image in textures if not image.packed_file]
    if missing or not textures:
        raise ValueError(f"Missing packed textures: {missing}")
    ground = [obj for obj in scene.objects if obj.name.startswith("Ground:")]
    if len(ground) != 1 or len(ground[0].data.polygons) < 100:
        raise ValueError("Expected cropped terrain surface")
    if not bpy.data.texts.get("SOURCE_NOTES.txt"):
        raise ValueError("Missing embedded provenance")
    report = {
        "status": "passed",
        "blender_version": bpy.app.version_string,
        "packed_textures": len(textures),
        "camera_type": scene.camera.data.type,
        "terrain_polygons": len(ground[0].data.polygons),
        "source_notes_embedded": True,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

"""Build a small real sparse DZI fixture for automated browser interoperability."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from chunk_pyramid import build_pyramid  # noqa: E402


def generate(directory):
    directory.mkdir(parents=True, exist_ok=True)
    layers = ["source_texture.v1", "geometry"]
    bounds = [-128, 512, 384, 640]
    chunks = []
    for column in (-1, 2):
        box = [column * 128, 512, (column + 1) * 128, 640]
        outputs = {}
        for layer in layers:
            name = f"{layer}-{column}.png"
            path = directory / name
            color = (60, 110, 150, 255) if layer == layers[0] else (185, 185, 185, 255)
            with Image.new("RGBA", (128, 128), color) as image:
                draw = ImageDraw.Draw(image)
                draw.rectangle((32, 32, 95, 95), fill=(235, 210, 110, 255))
                image.save(path)
            outputs[layer] = {
                "path": name,
                "width": 128,
                "height": 128,
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        chunks.append({"id": f"x{column}_y4", "bounds_px": box, "outputs": outputs})
    index = {
        "schema_version": 1,
        "status": "complete",
        "ai_styling": False,
        "config_sha256": "a" * 64,
        "bounds_px": bounds,
        "chunk_size_px": 128,
        "layers": layers,
        "chunks": chunks,
    }
    index_path = directory / "render-index.json"
    index_path.write_text(json.dumps(index))
    output = directory / "pyramid"
    metadata = {
        "title": "Synthetic chunk fixture",
        "place": {"name": "Synthetic fixture"},
        "layer_labels": {layers[0]: "Source textures", layers[1]: "Geometry only"},
        "attribution": [
            {"label": "Synthetic automated-test data", "url": "https://example.com/"}
        ],
        "coverage": {
            "label": "Two synthetic chunks with a deliberate gap",
            "bbox": [139.68, 35.68, 139.70, 35.70],
        },
        "source_edition": "Automated test fixture, not geographic imagery",
        "ai_styling": False,
        "limitations": "Synthetic colors verify image coordinates only.",
        "notice_text": "Synthetic automated-test pixels. Not geographic imagery.",
    }
    build_pyramid(index_path, output, tile_size=64, viewer_metadata=metadata)
    manifest = json.loads((output / "manifest.json").read_text())
    return {"directory": str(output), "manifest": manifest}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(generate(args.output)))

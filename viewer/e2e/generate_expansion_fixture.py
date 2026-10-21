"""Native 1024px cells in a sparse 5120px atlas with a stable old home frame."""

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
    layers = ["styled", "textured"]
    bounds = [-1024, -1024, 4096, 4096]
    cells = [(x, y) for y in range(3) for x in range(4)]
    cells += [(-1, -1), (-1, 1), (-1, 3)]
    chunks = []
    for x, y in cells:
        outputs = {}
        for layer in layers:
            name = f"{layer}-{x}-{y}.png"
            path = directory / name
            color = (115, 176, 167) if layer == "styled" else (175, 185, 198)
            with Image.new("RGB", (1024, 1024), color) as image:
                draw = ImageDraw.Draw(image)
                draw.rectangle(
                    (160, 160, 840, 840), fill=((x + 1) * 45, (y + 1) * 45, 100)
                )
                draw.line((0, 512, 1023, 512), fill=(240, 220, 180), width=24)
                image.save(path)
            outputs[layer] = {
                "path": name,
                "width": 1024,
                "height": 1024,
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        chunks.append(
            {
                "id": f"x{x}_y{y}",
                "bounds_px": [x * 1024, y * 1024, (x + 1) * 1024, (y + 1) * 1024],
                "outputs": outputs,
            }
        )
    index = {
        "schema_version": 1,
        "status": "complete",
        "ai_styling": True,
        "config_sha256": "a" * 64,
        "bounds_px": bounds,
        "chunk_size_px": 1024,
        "layers": layers,
        "chunks": chunks,
    }
    index_path = directory / "render-index.json"
    index_path.write_text(json.dumps(index))
    output = directory / "pyramid"
    metadata = {
        "title": "Synthetic native expansion",
        "place": {"name": "Shinjuku"},
        "layer_labels": {"styled": "Pixel city", "textured": "Source render"},
        "attribution": [{"label": "Synthetic pixels", "url": "https://example.com/"}],
        "coverage": {
            "label": "Synthetic expansion with deliberate coverage gaps",
            "bbox": [139.68, 35.68, 139.70, 35.70],
        },
        "source_edition": "Synthetic browser fixture, not geographic imagery",
        "ai_styling": True,
        "limitations": "Synthetic colors test coordinates, not geographic detail.",
        "notice_text": "Synthetic pixels. Not geographic imagery.",
    }
    build_pyramid(index_path, output, viewer_metadata=metadata)
    manifest = json.loads((output / "manifest.json").read_text())
    manifest.update(
        {
            "comparison_mode": "shared-framing-unverified",
            "coordinate_frame": {"id": "shinjuku-native-v1", "units": "pixels"},
            "legacy_view_bounds": [0, 0, 3072, 3072],
            "home_bounds": [0, 0, 3072, 3072],
        }
    )
    return {"directory": str(output), "manifest": manifest}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    print(json.dumps(generate(parser.parse_args().output)))

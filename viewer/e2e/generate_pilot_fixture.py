"""Small native-grid pilot fixture with source and AI layers in one frame."""

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from build_atlas import pyramid  # noqa: E402


def generate(directory):
    directory.mkdir(parents=True, exist_ok=True)
    layers = []
    for name, color in (("styled", (115, 176, 167)), ("textured", (175, 185, 198))):
        with Image.new("RGB", (3072, 3072), color) as image:
            draw = ImageDraw.Draw(image)
            for y in range(3):
                for x in range(3):
                    left, top = x * 1024 + 160, y * 1024 + 160
                    draw.rectangle(
                        (left, top, left + 680, top + 680),
                        fill=(x * 60, 80 + y * 50, 100),
                    )
            pyramid(image, directory, name, tile_size=256)
        layers.append(
            {
                "id": name,
                "label": "Pixel city" if name == "styled" else "Source render",
                "dzi": name + ".dzi",
            }
        )
    manifest = {
        "schema_version": 1,
        "id": "pilot-fixture",
        "title": "Shinjuku nine-patch pilot",
        "place": {"name": "Shinjuku"},
        "width": 3072,
        "height": 3072,
        "tile_size": 256,
        "overlap": 1,
        "format": "webp",
        "layers": layers,
        "ai_styling": True,
        "comparison_mode": "shared-framing-unverified",
        "artwork_approved": False,
        "source_coverage_verified": False,
        "coverage": {
            "label": "Bounded nine-patch pilot, synthetic fixture",
            "bbox": [139.68, 35.68, 139.70, 35.70],
        },
        "source_edition": "Synthetic test image, not geographic imagery",
        "attribution": [
            {"label": "Synthetic test pixels", "url": "https://example.com/"}
        ],
        "notice": "NOTICE.txt",
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    (directory / "NOTICE.txt").write_text("Synthetic test pixels. Not geographic data.")
    return {"directory": str(directory)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    print(json.dumps(generate(parser.parse_args().output)))

"""Build independent, differently sized DZI views for collection browser tests."""

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from build_atlas import pyramid  # noqa: E402


def generate(directory):
    for name, size, layer, styling in [
        ("pixel-city", (384, 384), "illustration", True),
        ("source", (256, 192), "textured", False),
    ]:
        output = directory / name
        output.mkdir(parents=True, exist_ok=True)
        with Image.new("RGB", size, (185, 220, 230)) as image:
            draw = ImageDraw.Draw(image)
            draw.rectangle((30, 30, 100, 100), fill=(40, 100, 180))
            draw.rectangle((110, 90, 180, 165), fill=(60, 140, 90))
            pyramid(image, output, layer, tile_size=64)
            image.save(output / "original.png")
        manifest = {
            "schema_version": 1,
            "id": f"fixture-{name}",
            "title": "Shinjuku preview fixture",
            "place": {"name": "Shinjuku", "name_ja": "新宿"},
            "width": size[0],
            "height": size[1],
            "tile_size": 64,
            "overlap": 1,
            "format": "webp",
            "layers": [
                {
                    "id": layer,
                    "label": "Pixel city" if styling else "Source textures",
                    "dzi": f"{layer}.dzi",
                    "original": "original.png",
                }
            ],
            "attribution": [
                {"label": "Synthetic test pixels", "url": "https://example.com/"}
            ],
            "coverage": {
                "label": "Bounded Shinjuku art study, synthetic fixture",
                "bbox": [139.68, 35.68, 139.70, 35.70],
            },
            "source_edition": "Synthetic test image, not geographic imagery",
            "ai_styling": styling,
            "artwork_approved": False,
            "source_coverage_verified": False,
            "notice": "NOTICE.txt",
        }
        (output / "manifest.json").write_text(json.dumps(manifest))
        (output / "NOTICE.txt").write_text("Synthetic test pixels. No geographic data.")
    collection = {
        "schema_version": 1,
        "kind": "atlas_collection",
        "id": "fixture-collection",
        "title": "Shinjuku preview",
        "default_view": "pixel-city",
        "views": [
            {
                "id": "pixel-city",
                "label": "Pixel city",
                "manifest": "pixel-city/manifest.json",
                "kind": "illustration-preview",
            },
            {
                "id": "source",
                "label": "Source",
                "manifest": "source/manifest.json",
                "kind": "source",
            },
        ],
    }
    (directory / "manifest.json").write_text(json.dumps(collection))
    return {"directory": str(directory), "collection": collection}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(generate(args.output)))

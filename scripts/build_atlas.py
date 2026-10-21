"""Build and verify a bounded source-image atlas for the browser viewer.

This exporter intentionally caps decoded input size. It is the sample-image
adapter, not a whole-city stitcher. Native pixels use lossless WebP; lower
levels use a single shared image grid and Lanczos resampling.
"""

import argparse
import hashlib
import json
import math
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from atlas import load_config
from PIL import Image, features
from PIL import __version__ as pillow_version
from prepare_previews import public_png

MAX_PIXELS = 16_000_000
VERSION = 1
LAYERS = (
    ("textured", "Source textures", "shinjuku-isometric.png"),
    ("geometry", "Geometry only", "shinjuku-geometry.png"),
)
ATTRIBUTION = [
    {
        "label": "Tokyo Metropolitan Government / Project PLATEAU, Shinjuku FY2025",
        "url": "https://www.geospatial.jp/ckan/dataset/plateau-13104-shinjuku-ku-2025",
    },
    {
        "label": "PLATEAU-Ortho 2023",
        "url": "https://docs.plateauview.mlit.go.jp/datasets/ortho/",
    },
    {
        "label": "Terrain: PLATEAU | Mapterhorn | 国土地理院",
        "url": "https://docs.plateauview.mlit.go.jp/datasets/terrain/",
    },
    {
        "label": "Source terms and modified-content notice",
        "url": "https://www.mlit.go.jp/plateau/site-policy/",
    },
]


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def level_sizes(width, height):
    if type(width) is not int or type(height) is not int or min(width, height) <= 0:
        raise ValueError("Image dimensions must be positive integers")
    maximum = (max(width, height) - 1).bit_length()
    return [
        (
            math.ceil(width / 2 ** (maximum - level)),
            math.ceil(height / 2 ** (maximum - level)),
        )
        for level in range(maximum + 1)
    ]


def tile_boxes(width, height, tile_size=256, overlap=1):
    if type(tile_size) is not int or tile_size < 1:
        raise ValueError("Tile size must be a positive integer")
    if type(overlap) is not int or not 0 <= overlap < tile_size:
        raise ValueError("Overlap must be smaller than a tile")
    for y in range(math.ceil(height / tile_size)):
        for x in range(math.ceil(width / tile_size)):
            yield (
                x,
                y,
                (
                    max(0, x * tile_size - overlap),
                    max(0, y * tile_size - overlap),
                    min(width, (x + 1) * tile_size + overlap),
                    min(height, (y + 1) * tile_size + overlap),
                ),
            )


def pyramid(image, directory, layer_id, tile_size=256, overlap=1):
    sizes = level_sizes(*image.size)
    for level, size in enumerate(sizes):
        folder = directory / f"{layer_id}_files" / str(level)
        folder.mkdir(parents=True)
        scaled = (
            image
            if size == image.size
            else image.resize(size, Image.Resampling.LANCZOS)
        )
        for x, y, box in tile_boxes(*size, tile_size, overlap):
            scaled.crop(box).save(
                folder / f"{x}_{y}.webp", "WEBP", lossless=True, method=4
            )
        if scaled is not image:
            scaled.close()
    root = ET.Element(
        "Image",
        {
            "xmlns": "http://schemas.microsoft.com/deepzoom/2008",
            "TileSize": str(tile_size),
            "Overlap": str(overlap),
            "Format": "webp",
        },
    )
    ET.SubElement(
        root, "Size", {"Width": str(image.width), "Height": str(image.height)}
    )
    ET.ElementTree(root).write(
        directory / f"{layer_id}.dzi", encoding="utf-8", xml_declaration=True
    )


def safe_asset(directory, name):
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError("Invalid asset path")
    path = directory / name
    if (
        Path(name).is_absolute()
        or ".." in Path(name).parts
        or not path.resolve().is_relative_to(directory.resolve())
    ):
        raise ValueError("Asset path escapes the atlas")
    return path


def release_identity(inputs, config, tile_size, tools):
    return {
        "exporter_version": VERSION,
        "tile_size": tile_size,
        "inputs": inputs,
        "config": config,
        "tools": tools,
    }


def release_name(identity):
    return (
        "shinjuku-source-"
        + hashlib.sha256(
            json.dumps(identity, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()[:16]
    )


def validated_config(path, dimensions):
    if path is None:
        return None
    config = load_config(path)
    left, top, right, bottom = config.bounds_px
    if (right - left, bottom - top) != dimensions:
        raise ValueError("Atlas configuration dimensions disagree with source imagery")
    return json.loads(path.read_text())


def verify_release(directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    inventory = json.loads((directory / "assets.json").read_text())
    if (
        manifest.get("schema_version") != VERSION
        or inventory.get("schema_version") != VERSION
    ):
        raise ValueError("Unsupported atlas version")
    width, height = manifest["width"], manifest["height"]
    sizes = level_sizes(width, height)
    tile_size, overlap = manifest["tile_size"], manifest["overlap"]
    expected = {"manifest.json", "NOTICE.txt", ".atlas-build"}
    if "atlas_config" in manifest:
        expected.add(manifest["atlas_config"])
    config = (
        validated_config(
            safe_asset(directory, manifest["atlas_config"]), (width, height)
        )
        if "atlas_config" in manifest
        else None
    )
    count = 0
    layer_ids = set()
    original_hashes = {}
    for layer in manifest["layers"]:
        layer_id = layer["id"]
        if layer_id in layer_ids or layer_id not in {item[0] for item in LAYERS}:
            raise ValueError("Invalid or duplicate layer")
        layer_ids.add(layer_id)
        dzi = safe_asset(directory, layer["dzi"])
        root = ET.parse(dzi).getroot()
        size = root.find("{http://schemas.microsoft.com/deepzoom/2008}Size")
        if (
            root.get("Format") != "webp"
            or root.get("TileSize") != str(tile_size)
            or root.get("Overlap") != str(overlap)
            or size is None
            or size.attrib != {"Width": str(width), "Height": str(height)}
        ):
            raise ValueError("Pyramid descriptor disagrees with manifest")
        expected.update((layer["dzi"], layer["original"]))
        original_hashes[Path(layer["original"]).name] = digest(
            safe_asset(directory, layer["original"])
        )
        with Image.open(safe_asset(directory, layer["original"])) as source:
            if source.size != (width, height):
                raise ValueError("Original dimensions disagree")
        for level, dimensions in enumerate(sizes):
            for x, y, box in tile_boxes(*dimensions, tile_size, overlap):
                name = f"{layer_id}_files/{level}/{x}_{y}.webp"
                expected.add(name)
                with Image.open(directory / name) as tile:
                    if tile.size != (box[2] - box[0], box[3] - box[1]):
                        raise ValueError(f"Wrong tile dimensions: {name}")
                    tile.load()
                count += 1
    if layer_ids != {item[0] for item in LAYERS}:
        raise ValueError("Both comparison layers are required")
    records = inventory["assets"]
    names = [record["path"] for record in records]
    actual = {
        str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file()
    }
    if (
        len(set(names)) != len(names)
        or set(names) != expected
        or actual != expected | {"assets.json"}
    ):
        raise ValueError("Missing, duplicate or unexpected assets")
    for record in records:
        path = safe_asset(directory, record["path"])
        if path.stat().st_size != record["bytes"] or digest(path) != record["sha256"]:
            raise ValueError(f"Asset checksum mismatch: {record['path']}")
    identity = release_identity(
        original_hashes, config, tile_size, manifest["identity"]["tools"]
    )
    if (
        identity != manifest["identity"]
        or original_hashes != manifest["source_hashes"]
        or release_name(identity) != manifest["id"]
    ):
        raise ValueError("Release identity disagrees with verified inputs")
    return {
        "id": manifest["id"],
        "tiles": count,
        "assets": len(records),
        "bytes": sum(r["bytes"] for r in records),
    }


def build_release(source, output, config_path=None, tile_size=256):
    # Reject bad configuration before any changes to an existing release.
    list(tile_boxes(1, 1, tile_size, 1))
    images = []
    try:
        for _, _, name in LAYERS:
            with Image.open(source / name) as image:
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError(
                        "Input exceeds bounded sample exporter limit; use chunked production"
                    )
                images.append(image.convert("RGB"))
        if images[0].size != images[1].size:
            raise ValueError("Comparison layers must have identical dimensions")
        config = validated_config(config_path, images[0].size)
        public_originals = {
            name: public_png((source / name).read_bytes()) for _, _, name in LAYERS
        }
        identity = release_identity(
            {
                name: hashlib.sha256(blob).hexdigest()
                for name, blob in public_originals.items()
            },
            config,
            tile_size,
            {"pillow": pillow_version, "webp": features.version_module("webp")},
        )
        release_id = release_name(identity)
        if output.exists() and (
            not output.is_dir() or not (output / ".atlas-build").is_file()
        ):
            raise ValueError(
                "Refusing to replace a directory not created by this exporter"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".atlas-stage-", dir=output.parent))
        try:
            (staging / ".atlas-build").write_text("isometric.tokyo source atlas\n")
            (staging / "originals").mkdir()
            layers = []
            for (layer_id, label, name), image in zip(LAYERS, images, strict=True):
                pyramid(image, staging, layer_id, tile_size)
                (staging / "originals" / name).write_bytes(public_originals[name])
                layers.append(
                    {
                        "id": layer_id,
                        "label": label,
                        "dzi": f"{layer_id}.dzi",
                        "original": f"originals/{name}",
                    }
                )
            notice = Path(__file__).resolve().parents[1] / "THIRD_PARTY_NOTICES.md"
            (staging / "NOTICE.txt").write_text(notice.read_text())
            manifest = {
                "schema_version": VERSION,
                "id": release_id,
                "title": "Shinjuku source atlas",
                "place": {"name": "Shinjuku", "name_ja": "新宿"},
                "width": images[0].width,
                "height": images[0].height,
                "tile_size": tile_size,
                "overlap": 1,
                "format": "webp",
                "layers": layers,
                "attribution": ATTRIBUTION,
                "coverage": {
                    "label": "Western Shinjuku, approximately 800 × 800 m",
                    "bbox": [139.6873, 35.6860, 139.6961, 35.6932],
                },
                "source_edition": "Shinjuku FY2025; PLATEAU-Ortho 2023",
                "ai_styling": False,
                "modifications": "Selected, transformed, cropped and rendered. Geometry comparison replaces materials. Independent project, no endorsement.",
                "limitations": "Mixed LOD and capture dates. Some buildings are untextured; trees are ground photography. The sample has cropped geographic edges.",
                "notice": "NOTICE.txt",
                "source_hashes": identity["inputs"],
                "identity": identity,
            }
            if config is not None:
                write_json(staging / "atlas-config.json", config)
                manifest["atlas_config"] = "atlas-config.json"
            write_json(staging / "manifest.json", manifest)
            records = [
                {
                    "path": str(p.relative_to(staging)),
                    "bytes": p.stat().st_size,
                    "sha256": digest(p),
                }
                for p in sorted(staging.rglob("*"))
                if p.is_file()
            ]
            write_json(
                staging / "assets.json", {"schema_version": VERSION, "assets": records}
            )
            result = verify_release(staging)
            # Existing output remains untouched until the complete replacement passes.
            backup = output.with_name(f".{output.name}-previous")
            if backup.exists():
                raise ValueError(
                    "Previous atlas backup exists; inspect it before replacing"
                )
            if output.exists():
                output.rename(backup)
            try:
                staging.rename(output)
            except BaseException:
                if backup.exists():
                    backup.rename(output)
                raise
            if backup.exists():
                shutil.rmtree(backup)
            return result
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    finally:
        for image in images:
            image.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("previews"))
    parser.add_argument("--output", type=Path, default=Path("viewer/public/atlas"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--tile-size", type=int, default=256)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = (
        verify_release(args.output)
        if args.verify
        else build_release(args.source, args.output, args.config, args.tile_size)
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

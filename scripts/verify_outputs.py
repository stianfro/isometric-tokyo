"""Verify downloaded tile hashes, lossless GLB extraction and completed renders."""

import argparse
import hashlib
import json
from pathlib import Path

from fetch_sample import unpack_b3dm
from PIL import Image, ImageStat


def verify_tile_hashes(directory, manifest):
    for tile in manifest["tiles"]:
        content = (directory / tile["path"]).read_bytes()
        if len(content) != tile["bytes"]:
            raise ValueError(f"Size mismatch: {tile['path']}")
        if hashlib.sha256(content).hexdigest() != tile["sha256"]:
            raise ValueError(f"SHA-256 mismatch: {tile['path']}")
        if "glb_path" in tile:
            glb, _, _ = unpack_b3dm(content)
            if glb != (directory / tile["glb_path"]).read_bytes():
                raise ValueError(f"GLB extraction mismatch: {tile['path']}")
    return len(manifest["tiles"])


def verify_image(path, expected_size):
    with Image.open(path) as source:
        if source.size != expected_size:
            raise ValueError(f"Unexpected render resolution: {path}")
        image = source.convert("RGB")
        if max(ImageStat.Stat(image).stddev) < 5:
            raise ValueError(f"Blank or near-uniform render: {path}")
        return {"size_px": list(image.size), "bytes": path.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifests = [
        Path("data/sample/manifest.json"),
        Path("data/ground/manifest.json"),
        Path("data/terrain/mesh.json"),
    ]
    checked = sum(
        verify_tile_hashes(path.parent, json.loads(path.read_text()))
        for path in manifests
    )
    metadata = json.loads((args.output / "render-metadata.json").read_text())
    if metadata["ai_styling"] is not False or metadata["triangles"] < 1000:
        raise ValueError("Invalid render metadata")
    if metadata["terrain_triangles"] < 10:
        raise ValueError("Expected real terrain geometry")
    size = (metadata["resolution_px"],) * 2
    images = {}
    for name in ("shinjuku-isometric", "shinjuku-detail", "shinjuku-geometry"):
        path = args.output / f"{name}.png"
        images[name] = verify_image(path, size)
    blend = args.output / "shinjuku-source.blend"
    if blend.stat().st_size < 1_000_000:
        raise ValueError("Blender project missing or unexpectedly small")
    report = {
        "status": "passed",
        "source_tile_hashes_checked": checked,
        "lossless_glb_extraction_checked": True,
        "images": images,
        "blend_bytes": blend.stat().st_size,
    }
    (args.output / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

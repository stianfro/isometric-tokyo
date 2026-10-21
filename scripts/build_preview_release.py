"""Build immutable, independently tiled illustration and source preview views.

This is a bounded preview exporter, never a geographic or artwork approval.
Only explicitly listed public inputs are eligible for inclusion.
"""

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from build_atlas import digest, level_sizes, pyramid, tile_boxes, write_json
from PIL import Image, features
from PIL import __version__ as pillow_version

VERSION = 1
MAX_PIXELS = 16_000_000
MAX_INPUT_BYTES = 100_000_000
INPUTS = {
    "previews/shinjuku-art/ai-pixel-city.png": "pixel-city/original.png",
    "previews/shinjuku-art/source-reference.png": "source/original.png",
    "previews/shinjuku-art/atlas-ink.png": "evidence/atlas-ink.png",
    "previews/shinjuku-art/ai-studies.json": "evidence/ai-studies.json",
    "previews/shinjuku-art/style-pilot.json": "evidence/style-pilot.json",
    "previews/shinjuku-art/source-coverage.json": "evidence/source-coverage.json",
    "previews/shinjuku-art/source-verification.json": "evidence/source-verification.json",
    "configs/art-direction.json": "evidence/art-direction.json",
    "configs/shinjuku-context.json": "evidence/source-config.json",
    "datasets/shinjuku-vegetation/vegetation.json.gz": "data/vegetation.json.gz",
    "datasets/shinjuku-vegetation/NOTICE.txt": "data/NOTICE.txt",
    "datasets/shinjuku-vegetation/README.txt": "data/README.txt",
    "datasets/shinjuku-vegetation/query.overpassql": "data/query.overpassql",
    "datasets/shinjuku-vegetation/SHA256SUMS": "data/SHA256SUMS",
    "THIRD_PARTY_NOTICES.md": "NOTICE.txt",
    "LICENSE": "LICENSE",
}
VIEWS = (
    ("pixel-city", "Pixel city", "illustration", "illustration-preview", True),
    ("source", "Source", "textured", "source", False),
)
ATTRIBUTION = [
    {
        "label": f"Tokyo Metropolitan Government / Project PLATEAU, {name} FY2025",
        "url": f"https://www.geospatial.jp/ckan/dataset/plateau-{code}-{slug}-ku-2025",
    }
    for name, code, slug in (
        ("Shinjuku", "13104", "shinjuku"),
        ("Shibuya", "13113", "shibuya"),
        ("Nakano", "13114", "nakano"),
    )
] + [
    {
        "label": "PLATEAU-Ortho 2023",
        "url": "https://docs.plateauview.mlit.go.jp/datasets/ortho/",
    },
    {
        "label": "Terrain: PLATEAU | Mapterhorn | 国土地理院",
        "url": "https://docs.plateauview.mlit.go.jp/datasets/terrain/",
    },
    {
        "label": "© OpenStreetMap contributors, ODbL",
        "url": "https://www.openstreetmap.org/copyright",
    },
    {
        "label": "Adapted vegetation database and terms",
        "url": "https://github.com/stianfro/isometric-tokyo/tree/main/datasets/shinjuku-vegetation",
    },
]


def canonical(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False
    ).encode()


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def safe_file(root, name):
    """Never traverse a symlink or accept alternate spellings of a public path."""
    if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_./-]+", name):
        raise ValueError("Unsafe asset path")
    if any(part in ("", ".", "..") for part in name.split("/")):
        raise ValueError("Unsafe asset path")
    path = root
    if path.is_symlink():
        raise ValueError("Symlink inputs and outputs are forbidden")
    for part in name.split("/"):
        path = path / part
        if path.is_symlink():
            raise ValueError("Symlink inputs and outputs are forbidden")
    return path


def snapshot(root):
    result = {}
    total = 0
    for source, destination in INPUTS.items():
        path = safe_file(root, source)
        total += path.stat().st_size
        if total > MAX_INPUT_BYTES:
            raise ValueError("Preview inputs exceed the byte limit")
        result[destination] = path.read_bytes()
    return result


def image_info(path):
    with Image.open(path) as image:
        if image.format != "PNG" or image.width * image.height > MAX_PIXELS:
            raise ValueError("Expected a bounded PNG preview")
        if image.mode != "RGB" or image.info:
            raise ValueError("Public originals must be RGB with no embedded metadata")
        image.load()
        return image.size


def validate_evidence(directory):
    """Bind the chosen view to recorded generation and source identities."""
    ai = json.loads((directory / "evidence/ai-studies.json").read_text())
    material = json.loads((directory / "evidence/style-pilot.json").read_text())
    selection = json.loads((directory / "evidence/art-direction.json").read_text())
    if (
        selection.get("preferred_direction") != "pixel-city"
        or selection.get("artwork_approved") is not False
        or selection.get("production_release") is not False
        or selection.get("geographic_fidelity_approved") is not False
        or ai.get("ai_styling") is not True
        or ai.get("production_release") is not False
        or material.get("ai_styling") is not False
    ):
        raise ValueError("Preview preference must not imply geographic or art approval")
    selected = [item for item in ai["candidates"] if item["id"] == "pixel-city"]
    sources = [item for item in material["outputs"] if item["id"] == "source-reference"]
    if len(selected) != 1 or len(sources) != 1:
        raise ValueError("Missing or duplicate provenance record")
    for view, record in (("pixel-city", selected[0]), ("source", sources[0])):
        path = directory / view / "original.png"
        dimensions = image_info(path)
        expected_size = record.get(
            "resolution_px", [record.get("width"), record.get("height")]
        )
        if digest(path) != record["sha256"] or list(dimensions) != expected_size:
            raise ValueError("Original image disagrees with provenance")
    reference = directory / "evidence/atlas-ink.png"
    image_info(reference)
    with Image.open(reference) as image:
        if (
            digest(reference) != ai["reference_public_png_sha256"]
            or sha(image.tobytes()) != ai["reference_rgb_sha256"]
        ):
            raise ValueError("AI reference identity mismatch")
    for line in (directory / "data/SHA256SUMS").read_text().splitlines():
        expected, name = line.split(maxsplit=1)
        if digest(safe_file(directory / "data", name.lstrip("*"))) != expected:
            raise ValueError("Vegetation database checksum mismatch")


def release_name(identity):
    return "shinjuku-preview-" + sha(canonical(identity))[:24]


def catalog(release_id):
    return {
        "schema_version": 1,
        "kind": "atlas_collection",
        "id": release_id,
        "title": "isometric.tokyo, Shinjuku preview",
        "default_view": "pixel-city",
        "production_release": False,
        "aligned_comparison": False,
        "views": [
            {
                "id": view,
                "label": label,
                "manifest": f"releases/{release_id}/{view}/manifest.json",
                "kind": kind,
            }
            for view, label, _, kind, _ in VIEWS
        ],
    }


def child_manifest(directory, release_id, view, label, layer, ai, tile_size):
    dimensions = image_info(directory / view / "original.png")
    config = json.loads((directory / "evidence/source-config.json").read_text())
    bbox = config.get("source_bounds_wsen")
    if (
        not isinstance(bbox, list)
        or len(bbox) != 4
        or any(type(n) not in (int, float) or not math.isfinite(n) for n in bbox)
        or not (-180 <= bbox[0] < bbox[2] <= 180 and -90 <= bbox[1] < bbox[3] <= 90)
    ):
        raise ValueError("Invalid geographic source bounds")
    return {
        "schema_version": 1,
        "id": f"{release_id}-{view}",
        "title": f"Shinjuku, {label}",
        "place": {"name": "Shinjuku", "name_ja": "新宿"},
        "width": dimensions[0],
        "height": dimensions[1],
        "tile_size": tile_size,
        "overlap": 1,
        "format": "webp",
        "layers": [
            {
                "id": layer,
                "label": label,
                "dzi": f"{layer}.dzi",
                "original": "original.png",
            }
        ],
        "attribution": ATTRIBUTION,
        "coverage": {
            "label": "Bounded western Shinjuku preview, not a complete district. Bounds describe source inputs, not an image georeference.",
            "bbox": bbox,
        },
        "source_edition": "Shinjuku, Shibuya and Nakano FY2025; PLATEAU-Ortho 2023; OSM 2026-10-21",
        "ai_styling": ai,
        "artwork_approved": False,
        "source_coverage_verified": False,
        "modifications": (
            "AI-assisted Pixel city illustration from an attributed source render. Roofs, facades, planting and surfaces are reinterpreted. Independent project, no endorsement."
            if ai
            else "Transformed source geometry and imagery, original elevations, mapped tree positions and artistic woodland crowns. Independent project, no endorsement."
        ),
        "limitations": (
            "Preferred aesthetic only. Geographic fidelity is unverified. Not aligned with the source view; no navigation or surveying use."
            if ai
            else "Source dates and LOD vary. Tree sizes and woodland crown placement are artistic. Real-world completeness and vertical disagreements remain unverified."
        ),
        "notice": "NOTICE.txt",
    }


def view_notice(notice):
    return (
        notice
        + "\n\nThis is an independent bounded preview, not approved geographic artwork.\nThe adapted ODbL vegetation database accompanies this release in data/.\n"
    )


def verify_version(directory, release_id):
    identity = json.loads((directory / "release.json").read_text())
    if (
        release_name(identity) != release_id
        or identity.get("exporter_version") != VERSION
    ):
        raise ValueError("Release identity mismatch")
    tile_size = identity["tile_size"]
    if type(tile_size) is not int or not 16 <= tile_size <= 2048:
        raise ValueError("Invalid tile size")
    if set(identity["inputs"]) != set(INPUTS.values()):
        raise ValueError("Unexpected release inputs")
    expected = set(INPUTS.values()) | {"release.json", "catalog.json"}
    if json.loads((directory / "catalog.json").read_text()) != catalog(release_id):
        raise ValueError("Catalog disagrees with release identity")
    for name, checksum in identity["inputs"].items():
        if digest(safe_file(directory, name)) != checksum:
            raise ValueError(f"Input checksum mismatch: {name}")
    validate_evidence(directory)
    tiles = 0
    for view, label, layer, _, ai in VIEWS:
        expected.update(
            (f"{view}/manifest.json", f"{view}/NOTICE.txt", f"{view}/{layer}.dzi")
        )
        manifest = json.loads((directory / view / "manifest.json").read_text())
        if manifest != child_manifest(
            directory, release_id, view, label, layer, ai, tile_size
        ):
            raise ValueError("View manifest disagrees with verified inputs")
        if (directory / view / "NOTICE.txt").read_text() != view_notice(
            (directory / "NOTICE.txt").read_text()
        ):
            raise ValueError("View notice disagrees with bound source notice")
        width, height = manifest["width"], manifest["height"]
        root = ET.parse(directory / view / f"{layer}.dzi").getroot()
        size = root.find("{http://schemas.microsoft.com/deepzoom/2008}Size")
        if (
            root.attrib
            != {"TileSize": str(tile_size), "Overlap": "1", "Format": "webp"}
            or size is None
            or size.attrib != {"Width": str(width), "Height": str(height)}
            or len(root) != 1
        ):
            raise ValueError("DZI descriptor mismatch")
        with Image.open(directory / view / "original.png") as original:
            sizes = level_sizes(width, height)
            for level, dimensions in enumerate(sizes):
                scaled = (
                    original
                    if dimensions == original.size
                    else original.resize(dimensions, Image.Resampling.LANCZOS)
                )
                for x, y, box in tile_boxes(*dimensions, tile_size, 1):
                    name = f"{view}/{layer}_files/{level}/{x}_{y}.webp"
                    expected.add(name)
                    with Image.open(safe_file(directory, name)) as tile:
                        tile.load()
                        if tile.format != "WEBP" or tile.size != (
                            box[2] - box[0],
                            box[3] - box[1],
                        ):
                            raise ValueError("Invalid tile format or dimensions")
                        if (
                            tile.mode != "RGB"
                            or tile.tobytes() != scaled.crop(box).tobytes()
                        ):
                            raise ValueError("Pyramid pixels changed")
                    tiles += 1
                if scaled is not original:
                    scaled.close()
    inventory = json.loads((directory / "assets.json").read_text())
    records = inventory.get("assets", [])
    actual = set()
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError("Symlink in release")
        if path.is_file():
            actual.add(path.relative_to(directory).as_posix())
    if (
        actual != expected | {"assets.json"}
        or len(records) != len(expected)
        or {r["path"] for r in records} != expected
    ):
        raise ValueError("Unexpected or missing release assets")
    for record in records:
        path = safe_file(directory, record["path"])
        if path.stat().st_size != record["bytes"] or digest(path) != record["sha256"]:
            raise ValueError(f"Asset checksum mismatch: {record['path']}")
    return {
        "id": release_id,
        "tiles": tiles,
        "assets": len(records) + 1,
        "bytes": sum(r["bytes"] for r in records)
        + (directory / "assets.json").stat().st_size,
    }


def verify_release(output):
    current = json.loads(safe_file(output, "manifest.json").read_text())
    release_id = current.get("id", "")
    if not re.fullmatch(
        r"shinjuku-preview-[a-f0-9]{24}", release_id
    ) or current != catalog(release_id):
        raise ValueError("Invalid active preview catalog")
    return verify_version(safe_file(output, f"releases/{release_id}"), release_id)


def build_release(repo_root, output, tile_size=256):
    if type(tile_size) is not int or not 16 <= tile_size <= 2048:
        raise ValueError("Invalid tile size")
    inputs = snapshot(repo_root)
    identity = {
        "exporter_version": VERSION,
        "tile_size": tile_size,
        "inputs": {name: sha(blob) for name, blob in inputs.items()},
        "tools": {"pillow": pillow_version, "webp": features.version_module("webp")},
        "code": {
            name: digest(Path(__file__).with_name(name))
            for name in ("build_preview_release.py", "build_atlas.py")
        },
    }
    release_id = release_name(identity)
    if output.is_symlink() or (
        output.exists()
        and (not output.is_dir() or not (output / ".preview-build").is_file())
    ):
        raise ValueError("Refusing an unowned output directory")
    output.mkdir(parents=True, exist_ok=True)
    safe_file(output, ".preview-build").write_text("isometric.tokyo bounded preview\n")
    releases = safe_file(output, "releases")
    releases.mkdir(exist_ok=True)
    destination = safe_file(output, f"releases/{release_id}")
    if destination.exists():
        result = verify_version(destination, release_id)
    else:
        staging = Path(tempfile.mkdtemp(prefix=".preview-stage-", dir=output))
        try:
            for name, blob in inputs.items():
                path = staging / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(blob)
            validate_evidence(staging)
            write_json(staging / "release.json", identity)
            write_json(staging / "catalog.json", catalog(release_id))
            for view, label, layer, _, ai in VIEWS:
                with Image.open(staging / view / "original.png") as image:
                    pyramid(image, staging / view, layer, tile_size)
                notice = view_notice(inputs["NOTICE.txt"].decode())
                (staging / view / "NOTICE.txt").write_text(notice)
                write_json(
                    staging / view / "manifest.json",
                    child_manifest(
                        staging, release_id, view, label, layer, ai, tile_size
                    ),
                )
            records = [
                {
                    "path": p.relative_to(staging).as_posix(),
                    "bytes": p.stat().st_size,
                    "sha256": digest(p),
                }
                for p in sorted(staging.rglob("*"))
                if p.is_file()
            ]
            write_json(
                staging / "assets.json", {"schema_version": VERSION, "assets": records}
            )
            result = verify_version(staging, release_id)
            if snapshot(repo_root) != inputs:
                raise ValueError("Inputs changed during preview build")
            staging.rename(destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    # A complete immutable version exists before its small active catalog changes.
    if snapshot(repo_root) != inputs:
        raise ValueError("Inputs changed during preview build")
    with tempfile.NamedTemporaryFile(
        mode="w", dir=output, prefix=".catalog-", delete=False
    ) as stream:
        temporary = Path(stream.name)
        stream.write(
            json.dumps(catalog(release_id), ensure_ascii=False, indent=2) + "\n"
        )
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, output / "manifest.json")
    finally:
        temporary.unlink(missing_ok=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--output", type=Path, default=Path("viewer/public/preview"))
    parser.add_argument("--tile-size", type=int, default=256)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = (
        verify_release(args.output)
        if args.verify
        else build_release(args.root, args.output, args.tile_size)
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

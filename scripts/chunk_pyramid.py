"""Bounded-memory sparse DZI pyramid export from finalized render chunks.

Native pixels are lossless. Lower levels use recursive aligned 2x2 BOX reduction,
not a full-canvas resize. This exports source chunks, not approved artwork.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from atlas import canonical_json, validate_bounds
from PIL import Image
from viewer_manifest import manifest_for, validate_metadata

ALGORITHM = "rgba-box2-sparse-v1"
MAX_CHUNK_PIXELS = 16_000_000
MAX_TILES = 1_000_000
MARKER = ".chunk-pyramid"
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
HASH = re.compile(r"^[0-9a-f]{64}$")


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            result.update(block)
    return result.hexdigest()


def safe_path(root, name):
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError("Expected a relative asset path")
    path = Path(name)
    result = root / path
    if (
        path.is_absolute()
        or ".." in path.parts
        or not result.resolve().is_relative_to(root.resolve())
    ):
        raise ValueError("Asset escapes its directory")
    return result


def integer(value, name, minimum=0, maximum=2**31 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def validate_settings(tile_size, overlap):
    integer(tile_size, "tile_size", 16, 1024)
    integer(overlap, "overlap", 0, min(16, tile_size - 1))


def intersect(a, b):
    bounds = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return bounds if bounds[0] < bounds[2] and bounds[1] < bounds[3] else None


def dimensions(width, height):
    maximum = (max(width, height) - 1).bit_length()
    return [
        (
            math.ceil(width / (2 ** (maximum - level))),
            math.ceil(height / (2 ** (maximum - level))),
        )
        for level in range(maximum + 1)
    ]


def tile_box(size, x, y, tile_size, overlap):
    return (
        max(0, x * tile_size - overlap),
        max(0, y * tile_size - overlap),
        min(size[0], (x + 1) * tile_size + overlap),
        min(size[1], (y + 1) * tile_size + overlap),
    )


def grid_positions(box, tile_size):
    for y in range(box[1] // tile_size, (box[3] - 1) // tile_size + 1):
        for x in range(box[0] // tile_size, (box[2] - 1) // tile_size + 1):
            yield x, y


def level_coverage(chunks, bounds, sizes, tile_size):
    """Return sparse covered tile cores; no enumeration of the empty canvas."""
    maximum = len(sizes) - 1
    result = []
    total = 0
    for level in range(maximum + 1):
        scale = 2 ** (maximum - level)
        positions = set()
        for chunk in chunks:
            box = chunk["bounds_px"]
            local = (
                (box[0] - bounds[0]) // scale,
                (box[1] - bounds[1]) // scale,
                math.ceil((box[2] - bounds[0]) / scale),
                math.ceil((box[3] - bounds[1]) / scale),
            )
            for position in grid_positions(local, tile_size):
                positions.add(position)
                if total + len(positions) > MAX_TILES:
                    raise ValueError("Sparse pyramid exceeds the tile-count limit")
        total += len(positions)
        result.append(positions)
    return result


def read_index(path):
    index = json.loads(path.read_text())
    if (
        type(index.get("schema_version")) is not int
        or index["schema_version"] != 1
        or index.get("status") != "complete"
    ):
        raise ValueError("Expected a finalized version 1 render index")
    bounds = validate_bounds(index["bounds_px"])
    size = integer(index["chunk_size_px"], "chunk_size_px", 1, 16384)
    layers = index["layers"]
    if (
        not isinstance(layers, list)
        or not layers
        or any(
            not isinstance(layer, str) or not NAME.fullmatch(layer) for layer in layers
        )
        or len(layers) != len(set(layers))
    ):
        raise ValueError("Expected unique safe layer identifiers")
    if not isinstance(index.get("config_sha256"), str) or not HASH.fullmatch(
        index["config_sha256"]
    ):
        raise ValueError("Expected a configuration SHA256")
    if not isinstance(index["chunks"], list) or not index["chunks"]:
        raise ValueError("At least one completed chunk is required")
    cells, identifiers, records = {}, set(), []
    for chunk in index["chunks"]:
        chunk_id = chunk["id"]
        if (
            not isinstance(chunk_id, str)
            or not NAME.fullmatch(chunk_id)
            or chunk_id in identifiers
        ):
            raise ValueError("Expected unique safe chunk identifiers")
        identifiers.add(chunk_id)
        box = validate_bounds(chunk["bounds_px"])
        cell = (box[0] // size, box[1] // size)
        expected = (
            max(bounds[0], cell[0] * size),
            max(bounds[1], cell[1] * size),
            min(bounds[2], (cell[0] + 1) * size),
            min(bounds[3], (cell[1] + 1) * size),
        )
        if box != expected or cell in cells:
            raise ValueError("Chunks must occupy unique, clipped global grid cells")
        width, height = box[2] - box[0], box[3] - box[1]
        if width * height > MAX_CHUNK_PIXELS:
            raise ValueError("Source chunk exceeds bounded decode limit")
        if set(chunk["outputs"]) != set(layers):
            raise ValueError("Every declared chunk must provide every layer")
        outputs = {}
        for layer in layers:
            output = chunk["outputs"][layer]
            asset = safe_path(path.parent, output["path"])
            if (
                output["width"] != width
                or output["height"] != height
                or output["bytes"] != asset.stat().st_size
                or output["sha256"] != digest(asset)
            ):
                raise ValueError("Chunk metadata or source checksum mismatch")
            with Image.open(asset) as image:
                if image.size != (width, height):
                    raise ValueError("Chunk image dimensions disagree with its bounds")
                image.verify()
            outputs[layer] = {"path": asset, "sha256": output["sha256"]}
        record = {"id": chunk_id, "bounds_px": list(box), "outputs": outputs}
        cells[cell] = record
        records.append(record)
    return index, records, cells


def source_record(chunk, layers):
    return {
        "id": chunk["id"],
        "bounds_px": chunk["bounds_px"],
        "hashes": {layer: chunk["outputs"][layer]["sha256"] for layer in layers},
    }


def paste_region(canvas, destination_box, source, source_box):
    region = intersect(destination_box, source_box)
    if region:
        cropped = source.crop(
            (
                region[0] - source_box[0],
                region[1] - source_box[1],
                region[2] - source_box[0],
                region[3] - source_box[1],
            )
        )
        canvas.paste(
            cropped, (region[0] - destination_box[0], region[1] - destination_box[1])
        )
        cropped.close()


def native_window(box, layer, bounds, chunk_size, cells, stats):
    global_box = tuple(value + bounds[i % 2] for i, value in enumerate(box))
    canvas = Image.new("RGBA", (box[2] - box[0], box[3] - box[1]))
    stats["largest_window_pixels"] = max(
        stats["largest_window_pixels"], canvas.width * canvas.height
    )
    for position in grid_positions(global_box, chunk_size):
        chunk = cells.get(position)
        if chunk is None:
            continue
        with Image.open(chunk["outputs"][layer]["path"]) as image:
            converted = image.convert("RGBA")
            paste_region(canvas, global_box, converted, chunk["bounds_px"])
            converted.close()
    return canvas


def tile_name(layer, level, x, y):
    return f"{layer}_files/{level}/{x}_{y}.webp"


def previous_window(
    directory, box, layer, level, size, coverage, tile_size, overlap, stats
):
    canvas = Image.new("RGBA", (box[2] - box[0], box[3] - box[1]))
    stats["largest_window_pixels"] = max(
        stats["largest_window_pixels"], canvas.width * canvas.height
    )
    clipped = intersect(box, (0, 0, *size))
    if clipped:
        for x, y in grid_positions(clipped, tile_size):
            if (x, y) not in coverage:
                continue
            expanded = tile_box(size, x, y, tile_size, overlap)
            core = tile_box(size, x, y, tile_size, 0)
            with Image.open(directory / tile_name(layer, level, x, y)) as tile:
                crop = tile.crop(
                    (
                        core[0] - expanded[0],
                        core[1] - expanded[1],
                        core[2] - expanded[0],
                        core[3] - expanded[1],
                    )
                )
                paste_region(canvas, box, crop, core)
                crop.close()
    return canvas


def write_descriptor(path, width, height, tile_size, overlap, sources, bounds, maximum):
    root = ET.Element(
        "Image",
        {
            "xmlns": "http://schemas.microsoft.com/deepzoom/2008",
            "TileSize": str(tile_size),
            "Overlap": str(overlap),
            "Format": "webp",
        },
    )
    ET.SubElement(root, "Size", {"Width": str(width), "Height": str(height)})
    for source in sources:
        box = source["bounds_px"]
        display = ET.SubElement(
            root, "DisplayRect", {"MinLevel": "0", "MaxLevel": str(maximum)}
        )
        ET.SubElement(
            display,
            "Rect",
            {
                "X": str(box[0] - bounds[0]),
                "Y": str(box[1] - bounds[1]),
                "Width": str(box[2] - box[0]),
                "Height": str(box[3] - box[1]),
            },
        )
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def identity(settings, sources):
    return (
        "chunks-"
        + hashlib.sha256(
            canonical_json({"settings": settings, "sources": sources}).encode()
        ).hexdigest()[:24]
    )


def verify_pyramid(directory):
    data = json.loads((directory / "pyramid-index.json").read_text())
    if data.get("schema_version") != 1 or data.get("algorithm") != ALGORITHM:
        raise ValueError("Unsupported chunk pyramid")
    settings, sources = data["settings"], data["sources"]
    if data["id"] != identity(settings, sources):
        raise ValueError("Pyramid identity mismatch")
    validate_settings(settings["tile_size"], settings["overlap"])
    bounds = validate_bounds(settings["bounds_px"])
    sizes = dimensions(bounds[2] - bounds[0], bounds[3] - bounds[1])
    coverage = level_coverage(sources, bounds, sizes, settings["tile_size"])
    expected = {MARKER} | {f"{layer}.dzi" for layer in settings["layers"]}
    if "viewer_metadata" in settings:
        expected.update({"manifest.json", "NOTICE.txt"})
        metadata = validate_metadata(settings["viewer_metadata"])
        expected_manifest = manifest_for(metadata, settings, sources, data["id"])
        if json.loads((directory / "manifest.json").read_text()) != expected_manifest:
            raise ValueError("Viewer manifest disagrees with chunk release")
        if (directory / "NOTICE.txt").read_text() != metadata["notice_text"]:
            raise ValueError("Source notice disagrees with viewer metadata")
    count = 0
    for layer in settings["layers"]:
        descriptor = ET.parse(safe_path(directory, f"{layer}.dzi")).getroot()
        size = descriptor.find("{http://schemas.microsoft.com/deepzoom/2008}Size")
        if (
            descriptor.get("Format") != "webp"
            or descriptor.get("TileSize") != str(settings["tile_size"])
            or descriptor.get("Overlap") != str(settings["overlap"])
            or size is None
            or size.attrib != {"Width": str(sizes[-1][0]), "Height": str(sizes[-1][1])}
        ):
            raise ValueError("DZI descriptor disagrees with pyramid settings")
        displays = descriptor.findall(
            "{http://schemas.microsoft.com/deepzoom/2008}DisplayRect"
        )
        expected_rectangles = []
        for source in sources:
            box = source["bounds_px"]
            expected_rectangles.append(
                {
                    "X": str(box[0] - bounds[0]),
                    "Y": str(box[1] - bounds[1]),
                    "Width": str(box[2] - box[0]),
                    "Height": str(box[3] - box[1]),
                }
            )
        actual_rectangles = []
        for display in displays:
            rectangle = display.find("{http://schemas.microsoft.com/deepzoom/2008}Rect")
            if (
                display.attrib != {"MinLevel": "0", "MaxLevel": str(len(sizes) - 1)}
                or rectangle is None
            ):
                raise ValueError("DZI sparse coverage disagrees with sources")
            actual_rectangles.append(rectangle.attrib)
        if actual_rectangles != expected_rectangles:
            raise ValueError("DZI sparse coverage disagrees with sources")
        for level, positions in enumerate(coverage):
            for x, y in positions:
                name = tile_name(layer, level, x, y)
                expected.add(name)
                box = tile_box(
                    sizes[level], x, y, settings["tile_size"], settings["overlap"]
                )
                with Image.open(safe_path(directory, name)) as image:
                    if image.size != (box[2] - box[0], box[3] - box[1]):
                        raise ValueError("Pyramid tile dimensions disagree")
                    image.verify()
                count += 1
    records = data["assets"]
    names = [record["path"] for record in records]
    actual = {
        str(path.relative_to(directory))
        for path in directory.rglob("*")
        if path.is_file()
    }
    if (
        len(set(names)) != len(names)
        or set(names) != expected
        or actual != expected | {"pyramid-index.json"}
    ):
        raise ValueError("Missing, duplicate or unexpected pyramid assets")
    for record in records:
        path = safe_path(directory, record["path"])
        if record["bytes"] != path.stat().st_size or record["sha256"] != digest(path):
            raise ValueError("Pyramid asset checksum mismatch")
    return {
        "id": data["id"],
        "tiles": count,
        "bytes": sum(record["bytes"] for record in records),
    }


def changed_regions(previous, sources, layer):
    old = {source["id"]: source for source in previous}
    new = {source["id"]: source for source in sources}
    result = []
    for key in old.keys() | new.keys():
        a, b = old.get(key), new.get(key)
        if (
            a is not None
            and b is not None
            and a["bounds_px"] == b["bounds_px"]
            and a["hashes"].get(layer) == b["hashes"].get(layer)
        ):
            continue
        for source in (a, b):
            if source is not None:
                result.append(source["bounds_px"])
    return result


def affected(box, level, maximum, regions, bounds):
    scale = 2 ** (maximum - level)
    for region in regions:
        local = (
            (region[0] - bounds[0]) // scale,
            (region[1] - bounds[1]) // scale,
            math.ceil((region[2] - bounds[0]) / scale),
            math.ceil((region[3] - bounds[1]) / scale),
        )
        if intersect(box, local):
            return True
    return False


def build_pyramid(
    index_path, output, tile_size=256, overlap=1, incremental=True, viewer_metadata=None
):
    validate_settings(tile_size, overlap)
    index_path, output = Path(index_path), Path(output)
    index, chunks, cells = read_index(index_path)
    if index_path.resolve().is_relative_to(output.resolve()) or any(
        asset["path"].resolve().is_relative_to(output.resolve())
        for chunk in chunks
        for asset in chunk["outputs"].values()
    ):
        raise ValueError("Pyramid output cannot contain its own source assets")
    if output.is_symlink() or (
        output.exists() and (not output.is_dir() or not (output / MARKER).is_file())
    ):
        raise ValueError("Refusing to replace a directory not created by this exporter")
    bounds, layers = index["bounds_px"], index["layers"]
    sizes = dimensions(bounds[2] - bounds[0], bounds[3] - bounds[1])
    maximum = len(sizes) - 1
    coverage = level_coverage(chunks, bounds, sizes, tile_size)
    sources = sorted(
        (source_record(chunk, layers) for chunk in chunks),
        key=lambda source: source["id"],
    )
    settings = {
        "algorithm": ALGORITHM,
        "config_sha256": index["config_sha256"],
        "bounds_px": bounds,
        "tile_size": tile_size,
        "overlap": overlap,
        "layers": layers,
    }
    if viewer_metadata is not None:
        settings["viewer_metadata"] = validate_metadata(viewer_metadata)
        if (
            type(index.get("ai_styling")) is not bool
            or index["ai_styling"] != settings["viewer_metadata"]["ai_styling"]
        ):
            raise ValueError(
                "Viewer AI styling provenance disagrees with the render index"
            )
        manifest_for(
            settings["viewer_metadata"], settings, sources, identity(settings, sources)
        )
    old = None
    if output.exists() and incremental:
        verify_pyramid(output)
        previous = json.loads((output / "pyramid-index.json").read_text())
        if previous["settings"] == settings:
            old = previous
    backup = output.with_name(f".{output.name}-previous")
    if backup.exists():
        raise ValueError("Previous pyramid backup exists; inspect it before replacing")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".chunk-pyramid-stage-", dir=output.parent))
    stats = {
        "generated_tiles": 0,
        "reused_tiles": 0,
        "largest_window_pixels": 0,
        "largest_source_chunk_pixels": max(
            (chunk["bounds_px"][2] - chunk["bounds_px"][0])
            * (chunk["bounds_px"][3] - chunk["bounds_px"][1])
            for chunk in chunks
        ),
    }
    try:
        (staging / MARKER).write_text(ALGORITHM + "\n")
        for layer in layers:
            changes = changed_regions(old["sources"], sources, layer) if old else []
            for level in range(maximum, -1, -1):
                for x, y in sorted(
                    coverage[level], key=lambda point: (point[1], point[0])
                ):
                    box = tile_box(sizes[level], x, y, tile_size, overlap)
                    name = tile_name(layer, level, x, y)
                    destination = staging / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if (
                        old
                        and not affected(box, level, maximum, changes, bounds)
                        and (output / name).is_file()
                    ):
                        shutil.copyfile(output / name, destination)
                        stats["reused_tiles"] += 1
                        continue
                    if level == maximum:
                        image = native_window(
                            box, layer, bounds, index["chunk_size_px"], cells, stats
                        )
                    else:
                        double = tuple(value * 2 for value in box)
                        window = previous_window(
                            staging,
                            double,
                            layer,
                            level + 1,
                            sizes[level + 1],
                            coverage[level + 1],
                            tile_size,
                            overlap,
                            stats,
                        )
                        image = window.resize(
                            (box[2] - box[0], box[3] - box[1]), Image.Resampling.BOX
                        )
                        window.close()
                    image.save(destination, "WEBP", lossless=True, exact=True, method=4)
                    image.close()
                    stats["generated_tiles"] += 1
            write_descriptor(
                staging / f"{layer}.dzi",
                *sizes[-1],
                tile_size,
                overlap,
                sources,
                bounds,
                maximum,
            )
        # Recheck sources before publishing, so edits during a long export do
        # not produce tiles claiming the hashes read at the beginning.
        for chunk in chunks:
            for asset in chunk["outputs"].values():
                if digest(asset["path"]) != asset["sha256"]:
                    raise ValueError("Source changed during pyramid construction")
        data = {
            "schema_version": 1,
            "algorithm": ALGORITHM,
            "id": identity(settings, sources),
            "settings": settings,
            "sources": sources,
            "source_config_sha256": index["config_sha256"],
            "source_coverage_verified": False,
            "artwork_approved": False,
            "stats": stats,
        }
        if "viewer_metadata" in settings:
            metadata = settings["viewer_metadata"]
            manifest = manifest_for(metadata, settings, sources, data["id"])
            (staging / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
            )
            (staging / "NOTICE.txt").write_text(metadata["notice_text"])
        data["assets"] = [
            {
                "path": str(path.relative_to(staging)),
                "bytes": path.stat().st_size,
                "sha256": digest(path),
            }
            for path in sorted(staging.rglob("*"))
            if path.is_file()
        ]
        (staging / "pyramid-index.json").write_text(json.dumps(data, indent=2) + "\n")
        result = verify_pyramid(staging) | stats
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tile-size", type=int, default=256)
    parser.add_argument("--overlap", type=int, default=1)
    parser.add_argument("--full-rebuild", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--viewer-metadata",
        type=Path,
        help="Explicit source credits and edition for a viewer-ready release",
    )
    args = parser.parse_args()
    if not args.verify and args.index is None:
        parser.error("--index is required unless verifying an existing pyramid")
    result = (
        verify_pyramid(args.output)
        if args.verify
        else build_pyramid(
            args.index,
            args.output,
            args.tile_size,
            args.overlap,
            not args.full_rebuild,
            json.loads(args.viewer_metadata.read_text())
            if args.viewer_metadata
            else None,
        )
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

"""Finalize bounded Blender atlas chunks and measure synthetic seam fixtures.

This module deliberately needs no Blender. PNG cropping happens after rendering
complete source meshes, and never changes a building to match an image boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath

from atlas import validate_bounds
from PIL import Image, ImageChops, ImageStat

MAX_ASSEMBLY_PIXELS = 4096 * 4096


def relative_asset(root: Path, name: str) -> Path:
    """Resolve a portable manifest asset without allowing directory escape."""
    if not isinstance(name, str) or "\\" in name:
        raise ValueError("Asset must be a relative POSIX path")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in (".", "..") for part in path.parts)
    ):
        raise ValueError("Asset must stay inside its output directory")
    result = root.joinpath(*path.parts).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError("Asset must stay inside its output directory")
    return result


def file_hash(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def check_crop(render_size, crop_box, core_bounds):
    """Check image-space cropping dimensions before writing any final pixels."""
    crop = validate_bounds(crop_box)
    core = validate_bounds(core_bounds)
    if len(render_size) != 2 or any(
        type(value) is not int or value < 1 for value in render_size
    ):
        raise ValueError("Render size must contain two positive integers")
    if not (
        0 <= crop[0] < crop[2] <= render_size[0]
        and 0 <= crop[1] < crop[3] <= render_size[1]
    ):
        raise ValueError("Crop extends beyond rendered image")
    size = crop[2] - crop[0], crop[3] - crop[1]
    if size != (core[2] - core[0], core[3] - core[1]):
        raise ValueError("Crop does not match global core pixel bounds")
    return crop


def validate_partition(bounds, chunks):
    """Require non-overlapping chunks to cover exactly the planned rectangle."""
    bounds = validate_bounds(bounds)
    seen, area = [], 0
    if not chunks:
        raise ValueError("No rendered chunks")
    if len(chunks) > 4096:
        raise ValueError("Bounded verifier supports at most 4096 chunks")
    for chunk in chunks:
        rectangle = validate_bounds(chunk["bounds_px"])
        left, top, right, bottom = rectangle
        if (
            left < bounds[0]
            or top < bounds[1]
            or right > bounds[2]
            or bottom > bounds[3]
        ):
            raise ValueError("Chunk extends beyond requested atlas bounds")
        if any(
            left < other[2]
            and right > other[0]
            and top < other[3]
            and bottom > other[1]
            for other in seen
        ):
            raise ValueError("Chunk rectangles overlap")
        seen.append(rectangle)
        area += (right - left) * (bottom - top)
    if area != (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]):
        raise ValueError("Chunk partition has gaps")


def finalize(index_path: Path) -> dict:
    """Crop finished frames, strip PNG metadata and record final file hashes."""
    root = index_path.parent
    index = json.loads(index_path.read_text())
    if index.get("schema_version") != 1 or index.get("status") not in (
        "rendered-overscan",
        "complete",
    ):
        raise ValueError("Expected a finished overscan render index")
    validate_partition(index["bounds_px"], index["chunks"])
    frames = index["chunks"] + ([index["reference"]] if index.get("reference") else [])
    layers = index.get("layers")
    if not isinstance(layers, list) or not layers or len(set(layers)) != len(layers):
        raise ValueError("Render index must declare distinct image layers")
    identifiers = [chunk["id"] for chunk in frames]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Render chunk identifiers must be unique")
    for chunk in frames:
        if set(chunk["frames"]) != set(layers):
            raise ValueError("Every chunk must contain every declared image layer")
        chunk["outputs"] = {}
        crop = check_crop(
            chunk["resolution_px"], chunk["crop_box_px"], chunk["bounds_px"]
        )
        for layer, frame in chunk["frames"].items():
            source = relative_asset(root, frame)
            destination = relative_asset(root, f"{layer}/{chunk['id']}.png")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(source) as image:
                image.load()
                if image.size != tuple(chunk["resolution_px"]):
                    raise ValueError(
                        "Rendered image dimensions do not match the camera plan"
                    )
                # Fresh RGB image discards filenames and other renderer metadata.
                cropped = image.convert("RGB").crop(crop)
                clean = Image.new("RGB", cropped.size)
                clean.paste(cropped)
                temporary = destination.with_suffix(".tmp")
                clean.save(temporary, format="PNG")
                temporary.replace(destination)
            chunk["outputs"][layer] = {
                "path": destination.relative_to(root.resolve()).as_posix(),
                "sha256": file_hash(destination),
                "width": crop[2] - crop[0],
                "height": crop[3] - crop[1],
                "bytes": destination.stat().st_size,
            }
    if index.get("guide_metadata"):
        from verify_guides import verify_guides

        verify_guides(index, root, compare_reference=bool(index.get("reference")))
    index["status"] = "complete"
    index["pixel_crop_after_geometry_render"] = True
    temporary_index = index_path.with_suffix(".tmp")
    temporary_index.write_text(json.dumps(index, indent=2, allow_nan=False) + "\n")
    temporary_index.replace(index_path)
    return index


def assemble_layer(index: dict, root: Path, layer: str) -> Image.Image:
    """Assemble only a small diagnostic patch, never an unbounded city image."""
    validate_partition(index["bounds_px"], index["chunks"])
    left, top, right, bottom = index["bounds_px"]
    width, height = right - left, bottom - top
    if width * height > MAX_ASSEMBLY_PIXELS:
        raise ValueError("Diagnostic assembly exceeds 4096x4096 pixel budget")
    canvas = Image.new("RGB", (width, height))
    for chunk in index["chunks"]:
        output = chunk["outputs"][layer]
        path = relative_asset(root, output["path"])
        if file_hash(path) != output["sha256"]:
            raise ValueError("Final chunk hash mismatch")
        with Image.open(path) as image:
            expected = (
                chunk["bounds_px"][2] - chunk["bounds_px"][0],
                chunk["bounds_px"][3] - chunk["bounds_px"][1],
            )
            if image.size != expected:
                raise ValueError("Final chunk dimensions changed")
            canvas.paste(
                image, (chunk["bounds_px"][0] - left, chunk["bounds_px"][1] - top)
            )
    return canvas


def difference_metrics(actual: Image.Image, reference: Image.Image) -> dict:
    if actual.size != reference.size:
        raise ValueError("Comparison dimensions must match")
    difference = ImageChops.difference(actual.convert("RGB"), reference.convert("RGB"))
    stats = ImageStat.Stat(difference)
    return {
        "mean_absolute_channel_error_8bit": sum(stats.mean) / 3,
        "maximum_channel_error_8bit": max(high for _, high in stats.extrema),
    }


def verify_seams(
    index: dict, root: Path, *, mean_limit: float = 1.0, seam_limit: float = 2.0
) -> dict:
    """Compare a 3x3 fixture with one full-view render, including boundary bands.

    Cycles sample noise can differ across image dimensions. Report measured
    residuals and gate mean errors rather than claiming bit-identical renders.
    """
    if (
        index.get("fixture") not in (None, "crossing-towers-shadow-v1")
        or len(index["chunks"]) != 9
        or "reference" not in index
    ):
        raise ValueError(
            "Seam verification requires a 3x3 render and full-view reference"
        )
    if not all(
        math.isfinite(value) and value >= 0 for value in (mean_limit, seam_limit)
    ):
        raise ValueError("Seam thresholds must be finite and nonnegative")
    result = {
        "fixture": index.get("fixture") or "source-chunk-comparison",
        "chunk_count": 9,
        "source_coverage_verified": False,
        "layers": {},
    }
    left, top, right, bottom = index["bounds_px"]
    edges_x = sorted(
        {
            chunk["bounds_px"][2] - left
            for chunk in index["chunks"]
            if chunk["bounds_px"][2] < right
        }
    )
    edges_y = sorted(
        {
            chunk["bounds_px"][3] - top
            for chunk in index["chunks"]
            if chunk["bounds_px"][3] < bottom
        }
    )
    if len(edges_x) != 2 or len(edges_y) != 2:
        raise ValueError("Fixture must have three rows and three columns")
    for layer in index["layers"]:
        canvas = assemble_layer(index, root, layer)
        reference_output = index["reference"]["outputs"][layer]
        reference_path = relative_asset(root, reference_output["path"])
        if file_hash(reference_path) != reference_output["sha256"]:
            raise ValueError("Reference image hash mismatch")
        with Image.open(reference_path) as reference:
            if max(ImageStat.Stat(reference.convert("RGB")).stddev) < 5:
                raise ValueError(
                    "Seam fixture reference is blank or lacks contrasting geometry"
                )
            metrics = difference_metrics(canvas, reference)
            bands = []
            for edge in edges_x:
                box = (max(0, edge - 2), 0, min(canvas.width, edge + 2), canvas.height)
                bands.append(
                    difference_metrics(canvas.crop(box), reference.crop(box))[
                        "mean_absolute_channel_error_8bit"
                    ]
                )
            for edge in edges_y:
                box = (0, max(0, edge - 2), canvas.width, min(canvas.height, edge + 2))
                bands.append(
                    difference_metrics(canvas.crop(box), reference.crop(box))[
                        "mean_absolute_channel_error_8bit"
                    ]
                )
            metrics["worst_boundary_band_mean_error_8bit"] = max(bands)
            metrics["passed"] = (
                metrics["mean_absolute_channel_error_8bit"] <= mean_limit
                and max(bands) <= seam_limit
            )
            result["layers"][layer] = metrics
            ImageChops.difference(canvas, reference.convert("RGB")).save(
                root / f"fixture-{layer}-difference.png"
            )
        canvas.save(root / f"fixture-{layer}-assembled.png")
    result["thresholds_8bit"] = {
        "whole_image_mean": mean_limit,
        "boundary_band_mean": seam_limit,
    }
    result["passed"] = all(value["passed"] for value in result["layers"].values())
    (root / "seam-verification.json").write_text(json.dumps(result, indent=2) + "\n")
    if not result["passed"]:
        raise ValueError(
            "Chunk renders differ from the full-view reference beyond seam thresholds"
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("finalize",))
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--verify-seams", action="store_true")
    parser.add_argument(
        "--assemble",
        action="store_true",
        help="Write bounded diagnostic mosaics, at most 4096x4096 pixels",
    )
    args = parser.parse_args()
    index = finalize(args.index)
    if args.verify_seams:
        print(json.dumps(verify_seams(index, args.index.parent), indent=2))
    if args.assemble:
        for layer in index["layers"]:
            assemble_layer(index, args.index.parent, layer).save(
                args.index.parent / f"atlas-{layer}.png"
            )
    print(
        f"Finalized {len(index['chunks'])} chunks; source coverage remains unverified"
    )


if __name__ == "__main__":
    main()

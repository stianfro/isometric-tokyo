"""Bounded seam-review regions and immutable, explicitly reviewed image repairs."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from atlas import canonical_json, validate_bounds
from chunk_pyramid import digest, intersect, paste_region, read_index, safe_path
from jobs import Ledger, artifact_path
from PIL import Image, ImageDraw
from PIL import __version__ as PILLOW_VERSION

MAX_REGION_PIXELS = 4_000_000
MAX_SIDE = 4096
MAX_SOURCE_CHUNKS = 64
MAX_INDEX_BYTES = 20_000_000
MAX_REPLACEMENT_BYTES = 32_000_000
REPAIRABLE_LAYERS = {"textured", "styled", "artwork"}
EDIT_METHODS = {"manual", "ai-assisted"}


def content_id(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def bounded_size(bounds):
    left, top, right, bottom = validate_bounds(bounds)
    width, height = right - left, bottom - top
    if width > MAX_SIDE or height > MAX_SIDE or width * height > MAX_REGION_PIXELS:
        raise ValueError("Review region exceeds the bounded image limit")
    return width, height


def clean_png(image, path):
    """Copy pixels, not metadata, color profiles, EXIF or source filenames."""
    rgba = image.convert("RGBA")
    clean = Image.frombytes("RGBA", rgba.size, rgba.tobytes())
    rgba.close()
    clean.save(path, format="PNG", compress_level=9)
    clean.close()
    return image_record(path)


def image_record(path):
    with Image.open(path) as image:
        width, height = image.size
    return {
        "path": path.name,
        "sha256": digest(path),
        "bytes": path.stat().st_size,
        "width": width,
        "height": height,
    }


def publish(staging, root, manifest):
    """Content-addressed publication: identical retries verify, never overwrite."""
    ident = content_id(manifest)
    manifest = {**manifest, "id": ident}
    write_json(staging / "manifest.json", manifest)
    destination = root / ident
    if destination.exists():
        existing = verify_bundle(destination, manifest["kind"])
        if existing != manifest:
            raise ValueError("Existing immutable review bundle differs")
    else:
        staging.rename(destination)
    return destination, manifest


def verify_bundle(directory, expected_kind=None):
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError("Immutable review bundle cannot be a symlink")
    path = directory / "manifest.json"
    if path.is_symlink() or path.stat().st_size > MAX_INDEX_BYTES:
        raise ValueError("Invalid review manifest")
    value = json.loads(path.read_text())
    ident = value.get("id")
    if (
        value.get("schema_version") != 1
        or value.get("kind") not in {"seam-review-region", "manual-repair"}
        or (expected_kind is not None and value["kind"] != expected_kind)
        or directory.name != ident
        or content_id({key: val for key, val in value.items() if key != "id"}) != ident
    ):
        raise ValueError("Review bundle identity mismatch")
    size = bounded_size(value["bounds_px"])
    files = value["files"]
    expected = {"manifest.json"}
    for record in files.values():
        asset = safe_path(directory, record["path"])
        if asset.is_symlink() or asset.parent != directory:
            raise ValueError("Review file must be a direct immutable asset")
        expected.add(record["path"])
        if (
            asset.stat().st_size != record["bytes"]
            or digest(asset) != record["sha256"]
            or (record["width"], record["height"]) != size
        ):
            raise ValueError("Review file checksum or dimensions mismatch")
        with Image.open(asset) as image:
            if image.format != "PNG" or image.size != size or image.info:
                raise ValueError("Review image must be metadata-free PNG")
            image.verify()
    if {path.name for path in directory.iterdir()} != expected:
        raise ValueError("Unexpected files in immutable review bundle")
    return value


def assemble_region(index_path, output, bounds, layers, *, workspace=Path(".")):
    """Assemble one bounded crop, keeping sparse cells explicit and guides intact."""
    workspace = Path(workspace).resolve()
    index_path = Path(index_path).resolve()
    if not index_path.is_relative_to(workspace):
        raise ValueError("Render index must be inside workspace")
    size = bounded_size(bounds)
    bounds = list(validate_bounds(bounds))
    if index_path.stat().st_size > MAX_INDEX_BYTES:
        raise ValueError("Render index exceeds metadata limit")
    index_hash = digest(index_path)
    index, chunks, _cells = read_index(index_path)
    if (
        not isinstance(layers, (tuple, list))
        or not 1 <= len(layers) <= 2
        or len(layers) != len(set(layers))
        or not set(layers) <= set(index["layers"])
    ):
        raise ValueError("Choose one or two distinct declared image layers")
    if intersect(bounds, index["bounds_px"]) != tuple(bounds):
        raise ValueError("Review bounds must be inside the atlas extent")
    chosen = [chunk for chunk in chunks if intersect(bounds, chunk["bounds_px"])]
    if not chosen or len(chosen) > MAX_SOURCE_CHUNKS:
        raise ValueError("Review needs between one and 64 intersecting chunks")
    sources = []
    for chunk in chosen:
        outputs = {}
        for layer in layers:
            source = chunk["outputs"][layer]["path"].resolve()
            if not source.is_relative_to(workspace):
                raise ValueError("Review source escapes workspace")
            outputs[layer] = {
                "path": source.relative_to(workspace).as_posix(),
                "sha256": chunk["outputs"][layer]["sha256"],
                "bytes": source.stat().st_size,
            }
        sources.append(
            {
                "id": chunk["id"],
                "bounds_px": chunk["bounds_px"],
                "intersection_px": list(intersect(bounds, chunk["bounds_px"])),
                "outputs": outputs,
            }
        )
    sources.sort(key=lambda value: value["id"])
    output = Path(output)
    if output.is_symlink():
        raise ValueError("Review output store cannot be a symlink")
    output.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".review-stage-", dir=output))
    try:
        files = {}
        covered = sum(
            (s["intersection_px"][2] - s["intersection_px"][0])
            * (s["intersection_px"][3] - s["intersection_px"][1])
            for s in sources
        )
        mask = Image.new("RGBA", size, (0, 0, 0, 255))
        draw = ImageDraw.Draw(mask)
        for source in sources:
            left, top, right, bottom = source["intersection_px"]
            draw.rectangle(
                (
                    left - bounds[0],
                    top - bounds[1],
                    right - bounds[0] - 1,
                    bottom - bounds[1] - 1,
                ),
                fill=(255, 255, 255, 255),
            )
        files["coverage"] = clean_png(mask, staging / "coverage.png")
        mask.close()
        for layer in layers:
            original = Image.new("RGBA", size)
            for chunk in chosen:
                with Image.open(chunk["outputs"][layer]["path"]) as source:
                    if (
                        source.format not in {"PNG", "WEBP", "JPEG"}
                        or source.mode not in {"RGB", "RGBA", "P", "L", "LA"}
                        or getattr(source, "n_frames", 1) != 1
                    ):
                        raise ValueError("Review requires static 8-bit source images")
                    rgba = source.convert("RGBA")
                    paste_region(original, bounds, rgba, chunk["bounds_px"])
                    rgba.close()
            files[f"original:{layer}"] = clean_png(
                original, staging / f"original-{layer}.png"
            )
            annotated = Image.new("RGBA", size, (48, 48, 48, 255))
            annotated.alpha_composite(original)
            original.close()
            draw = ImageDraw.Draw(annotated)
            for source in sources:
                left, top, right, bottom = source["bounds_px"]
                # Only original chunk borders, not arbitrary crop edges.
                for x in (left, right):
                    if bounds[0] <= x < bounds[2]:
                        draw.line(
                            (
                                x - bounds[0],
                                top - bounds[1],
                                x - bounds[0],
                                bottom - bounds[1] - 1,
                            ),
                            fill=(255, 45, 80, 255),
                        )
                for y in (top, bottom):
                    if bounds[1] <= y < bounds[3]:
                        draw.line(
                            (
                                left - bounds[0],
                                y - bounds[1],
                                right - bounds[0] - 1,
                                y - bounds[1],
                            ),
                            fill=(255, 45, 80, 255),
                        )
            files[f"boundaries:{layer}"] = clean_png(
                annotated, staging / f"boundaries-{layer}.png"
            )
            annotated.close()
        # Detect edits between provenance collection and pixel assembly.
        if digest(index_path) != index_hash or any(
            digest(artifact_path(workspace, record["path"])) != record["sha256"]
            for source in sources
            for record in source["outputs"].values()
        ):
            raise ValueError("Review source changed during assembly")
        manifest = {
            "schema_version": 1,
            "kind": "seam-review-region",
            "algorithm": "bounded-native-pixels-v1",
            "encoder": {"pillow": PILLOW_VERSION},
            "bounds_px": bounds,
            "layers": list(layers),
            "config_sha256": index["config_sha256"],
            "source_index": {
                "path": index_path.relative_to(workspace).as_posix(),
                "sha256": index_hash,
            },
            "source_chunks": sources,
            "covered_pixels": covered,
            "missing_pixels": size[0] * size[1] - covered,
            "source_coverage_verified": False,
            "artwork_approved": False,
            "files": files,
        }
        destination, manifest = publish(staging, output, manifest)
        return {"directory": str(destination), "manifest": manifest}
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def verify_source_jobs(ledger, region, layer, source_jobs, workspace):
    if not source_jobs or len(source_jobs) != len(set(source_jobs)):
        raise ValueError("Unique source job IDs are required")
    unmatched = {source["id"]: source for source in region["source_chunks"]}
    for ident in source_jobs:
        job = ledger.get(ident)
        payload = job["payload"]
        source = unmatched.pop(payload.get("chunk_id"), None)
        if (
            source is None
            or job["state"] not in {"needs_review", "approved"}
            or payload.get("bounds_px") != source["bounds_px"]
            or payload.get("config_sha256") != region["config_sha256"]
            or payload.get("render_index_sha256") != region["source_index"]["sha256"]
        ):
            raise ValueError("Source job does not match the immutable region inputs")
        wanted = source["outputs"][layer]
        if wanted not in job["artifacts"]:
            raise ValueError("Source job lacks the exact selected chunk artifact")
        for record in job["artifacts"]:
            path = artifact_path(workspace, record["path"])
            if (
                path.stat().st_size != record["bytes"]
                or digest(path) != record["sha256"]
            ):
                raise ValueError("Source job artifact changed after completion")
    if unmatched:
        raise ValueError("Every contributing chunk needs a source job dependency")


def import_repair(
    region_path,
    replacement,
    bounds,
    layer,
    source_jobs,
    database,
    output,
    *,
    workspace=Path("."),
    note,
    edit_method="manual",
):
    """Register a new manual candidate. Never overwrite sources, guides or approvals."""
    workspace = Path(workspace).resolve()
    region_path = Path(region_path).resolve()
    output = Path(output).resolve()
    if not region_path.is_relative_to(workspace) or not output.is_relative_to(
        workspace
    ):
        raise ValueError("Repair region and output must be inside workspace")
    region = verify_bundle(region_path, "seam-review-region")
    if list(validate_bounds(bounds)) != region["bounds_px"]:
        raise ValueError("Replacement bounds do not exactly match the review region")
    if layer not in REPAIRABLE_LAYERS or layer not in region["layers"]:
        raise ValueError(
            "Only an explicit artwork layer can be repaired, not geometry or guides"
        )
    if not isinstance(edit_method, str) or edit_method not in EDIT_METHODS:
        raise ValueError("Explicit edit method must be manual or ai-assisted")
    if not isinstance(note, str) or not note.strip() or len(note) > 2000:
        raise ValueError("A repair note of one to 2000 characters is required")
    if region["missing_pixels"]:
        raise ValueError(
            "Sparse review regions cannot be repaired as if coverage existed"
        )
    replacement = Path(replacement)
    if replacement.stat().st_size > MAX_REPLACEMENT_BYTES:
        raise ValueError("Replacement exceeds encoded image limit")
    input_hash = digest(replacement)
    size = bounded_size(bounds)
    with Image.open(replacement) as image:
        if (
            image.size != size
            or image.format not in {"PNG", "WEBP"}
            or getattr(image, "n_frames", 1) != 1
        ):
            raise ValueError(
                "Replacement must be a single PNG/WebP with exact dimensions"
            )
        image.verify()
    ledger = Ledger(database)
    staging = None
    try:
        verify_source_jobs(ledger, region, layer, source_jobs, workspace)
        output.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".repair-stage-", dir=output))
        files = {}
        for name, key in (
            ("source", f"original:{layer}"),
            ("boundaries", f"boundaries:{layer}"),
        ):
            source = safe_path(region_path, region["files"][key]["path"])
            shutil.copyfile(source, staging / f"{name}.png")
            files[name] = image_record(staging / f"{name}.png")
        with Image.open(replacement) as image:
            files["repaired"] = clean_png(image, staging / "repaired.png")
        if digest(replacement) != input_hash:
            raise ValueError("Replacement changed during import")
        verify_bundle(region_path, "seam-review-region")
        verify_source_jobs(ledger, region, layer, source_jobs, workspace)
        manifest = {
            "schema_version": 1,
            "kind": "manual-repair",
            "algorithm": "manual-rgba-pixels-v1",
            "encoder": {"pillow": PILLOW_VERSION},
            "bounds_px": list(bounds),
            "layer": layer,
            "config_sha256": region["config_sha256"],
            "source_region_id": region["id"],
            "source_chunks": region["source_chunks"],
            "source_jobs": sorted(source_jobs),
            "replacement_input_sha256": input_hash,
            "note": note.strip(),
            "edit_method": edit_method,
            "artwork_approved": False,
            "guides_modified": False,
            "files": files,
        }
        destination, manifest = publish(staging, output, manifest)
        paths = [
            (destination / record["path"]).relative_to(workspace).as_posix()
            for record in manifest["files"].values()
        ]
        paths.append((destination / "manifest.json").relative_to(workspace).as_posix())
        ident = ledger.import_completed(
            "manual-repair",
            region["id"],
            {
                "repair_id": manifest["id"],
                "bounds_px": list(bounds),
                "layer": layer,
                "config_sha256": region["config_sha256"],
                "source_region_id": region["id"],
                "note": note.strip(),
                "edit_method": edit_method,
                "review_scope": "Manual artwork candidate. Source pixels and guides remain unchanged.",
            },
            workspace,
            paths,
            dependencies=source_jobs,
            invalidate_dependents_of=source_jobs,
            invalidation_note="Manual repair candidate requires derived work to be rebuilt from its reviewed version",
        )
        return {
            "job_id": ident,
            "state": ledger.get(ident)["state"],
            "repair_id": manifest["id"],
            "directory": str(destination),
        }
    finally:
        ledger.close()
        if staging is not None and staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path("."))
    sub = parser.add_subparsers(dest="action", required=True)
    assemble = sub.add_parser("assemble")
    assemble.add_argument("--index", required=True, type=Path)
    assemble.add_argument("--bounds", required=True, nargs=4, type=int)
    assemble.add_argument("--layers", required=True, nargs="+")
    assemble.add_argument("--output", type=Path, default=Path(".cache/review-regions"))
    repair = sub.add_parser("import-repair")
    repair.add_argument("--region", required=True, type=Path)
    repair.add_argument("--replacement", required=True, type=Path)
    repair.add_argument("--bounds", required=True, nargs=4, type=int)
    repair.add_argument("--layer", required=True)
    repair.add_argument("--source-jobs", required=True, nargs="+")
    repair.add_argument("--note", required=True)
    repair.add_argument("--edit-method", choices=sorted(EDIT_METHODS), required=True)
    repair.add_argument("--database", type=Path, default=Path(".cache/jobs.sqlite"))
    repair.add_argument("--output", type=Path, default=Path(".cache/repairs"))
    verify = sub.add_parser("verify")
    verify.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.action == "assemble":
        result = assemble_region(
            args.index, args.output, args.bounds, args.layers, workspace=args.workspace
        )
        result = {
            "directory": result["directory"],
            "id": result["manifest"]["id"],
            "missing_pixels": result["manifest"]["missing_pixels"],
        }
    elif args.action == "verify":
        result = verify_bundle(args.directory)
        result = {
            "id": result["id"],
            "kind": result["kind"],
            "files": len(result["files"]),
        }
    else:
        result = import_repair(
            args.region,
            args.replacement,
            args.bounds,
            args.layer,
            args.source_jobs,
            args.database,
            args.output,
            workspace=args.workspace,
            note=args.note,
            edit_method=args.edit_method,
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

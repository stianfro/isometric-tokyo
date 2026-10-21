"""Prepare and inspect a bounded native-resolution nine-patch illustration pilot.

This script never generates or resizes artwork. Model calls are external and
their full native outputs are retained. Accepted core pixels are supplied as
context to the next call, then preserved by non-overlapping exact crops.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image

SIZE = 3072
CORE = 1024
WINDOW = 1254
ORDER = (
    "x1_y1",
    "x1_y0",
    "x0_y1",
    "x2_y1",
    "x1_y2",
    "x0_y0",
    "x2_y0",
    "x0_y2",
    "x2_y2",
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def clean_png(image, path):
    """Discard ancillary metadata without changing RGB pixels."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = Image.new("RGB", image.size)
    clean.paste(image.convert("RGB"))
    clean.save(path)


def grid():
    result = {}
    for row in range(3):
        for column in range(3):
            x, y = column * CORE, row * CORE
            left = min(max(x - (WINDOW - CORE) // 2, 0), SIZE - WINDOW)
            top = min(max(y - (WINDOW - CORE) // 2, 0), SIZE - WINDOW)
            result[f"x{column}_y{row}"] = {
                "id": f"x{column}_y{row}",
                "bounds_px": [x, y, x + CORE, y + CORE],
                "source_bounds_px": [left, top, left + WINDOW, top + WINDOW],
                "crop_box_px": [x - left, y - top, x - left + CORE, y - top + CORE],
            }
    return result


def initialize(directory, source, structure, style):
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Pilot requires an empty output directory")
    directory.mkdir(parents=True, exist_ok=True)
    for path, name, expected in (
        (source, "source.png", (SIZE, SIZE)),
        (structure, "structure.png", (SIZE, SIZE)),
        (style, "style.png", None),
    ):
        with Image.open(path) as image:
            if expected and image.size != expected:
                raise ValueError("Source must be a native 3072 square render")
            clean_png(image, directory / "inputs" / name)
    plan = {
        "schema_version": 1,
        "bounds_px": [0, 0, SIZE, SIZE],
        "chunk_size_px": CORE,
        "generation_window_px": WINDOW,
        "generation_order": list(ORDER),
        "max_main_calls": 9,
        "max_repair_calls": 4,
        "artwork_approved": False,
        "geographic_fidelity_approved": False,
        "patches": grid(),
        "inputs": {
            name: digest(directory / "inputs" / name)
            for name in ("source.png", "structure.png", "style.png")
        },
    }
    write_json(directory / "plan.json", plan)
    return plan


def load_plan(directory):
    plan = json.loads((directory / "plan.json").read_text())
    if (
        plan.get("patches") != grid()
        or plan.get("generation_order") != list(ORDER)
        or set(plan.get("inputs", {})) != {"source.png", "structure.png", "style.png"}
    ):
        raise ValueError("Pilot plan does not match the bounded grid")
    for name, expected in plan["inputs"].items():
        if digest(directory / "inputs" / name) != expected:
            raise ValueError("Pilot source or style reference changed")
    return plan


def intersection(a, b):
    box = [max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])]
    return box if box[0] < box[2] and box[1] < box[3] else None


def prepare(directory, identifier):
    """Combine source structure with already accepted cores as model context."""
    directory = Path(directory)
    plan = load_plan(directory)
    patch = plan["patches"][identifier]
    if (directory / "records" / f"{identifier}.json").exists():
        raise ValueError("An accepted core is immutable; use a new pilot for retries")
    box = patch["source_bounds_px"]
    with Image.open(directory / "inputs/structure.png") as source:
        context = source.crop(box).convert("RGB")
    anchors = []
    for prior in ORDER[: ORDER.index(identifier)]:
        record_path = directory / "records" / f"{prior}.json"
        if not record_path.exists():
            raise ValueError("Generate and inspect earlier cores before this patch")
        record = json.loads(record_path.read_text())
        core_path = directory / "styled" / f"{prior}.png"
        if digest(core_path) != record["styled_sha256"]:
            raise ValueError("Accepted context pixels changed")
        other = plan["patches"][prior]["bounds_px"]
        overlap = intersection(box, other)
        if not overlap:
            continue
        with Image.open(core_path) as image:
            crop = [
                overlap[0] - other[0],
                overlap[1] - other[1],
                overlap[2] - other[0],
                overlap[3] - other[1],
            ]
            context.paste(image.crop(crop), (overlap[0] - box[0], overlap[1] - box[1]))
        anchors.append(
            {
                "id": prior,
                "bounds_px": overlap,
                "local_bounds_px": [
                    overlap[0] - box[0],
                    overlap[1] - box[1],
                    overlap[2] - box[0],
                    overlap[3] - box[1],
                ],
                "sha256": record["styled_sha256"],
            }
        )
    context_path = directory / "inputs" / f"{identifier}-context.png"
    source_path = directory / "inputs" / f"{identifier}-source.png"
    clean_png(context, context_path)
    with Image.open(directory / "inputs/source.png") as image:
        clean_png(image.crop(box), source_path)
    reference = {
        **patch,
        "anchors": anchors,
        "reference_sha256": [
            digest(context_path),
            digest(source_path),
            plan["inputs"]["style.png"],
        ],
    }
    write_json(directory / "inputs" / f"{identifier}.json", reference)
    return reference


def ingest(directory, identifier, generated, prompt):
    directory = Path(directory)
    load_plan(directory)
    record_path = directory / "records" / f"{identifier}.json"
    if record_path.exists():
        raise ValueError("Accepted native outputs cannot be overwritten")
    reference = json.loads((directory / "inputs" / f"{identifier}.json").read_text())
    if any(reference.get(key) != value for key, value in grid()[identifier].items()):
        raise ValueError("Prepared reference differs from the fixed source grid")
    for suffix, expected in zip(
        ("context", "source"), reference["reference_sha256"][:2], strict=True
    ):
        if digest(directory / "inputs" / f"{identifier}-{suffix}.png") != expected:
            raise ValueError("Generation reference changed after preparation")
    output_path = directory / "generation" / f"{identifier}.png"
    core_path = directory / "styled" / f"{identifier}.png"
    with Image.open(generated) as image:
        if image.size != (WINDOW, WINDOW):
            raise ValueError(
                f"Expected native {WINDOW} square output, got {image.size}; no resizing is allowed"
            )
        clean_png(image, output_path)
        clean_png(image.crop(reference["crop_box_px"]), core_path)
    with Image.open(directory / "inputs/source.png") as image:
        clean_png(
            image.crop(reference["bounds_px"]),
            directory / "textured" / f"{identifier}.png",
        )
    record = {
        **reference,
        "styled_sha256": digest(core_path),
        "source_sha256": digest(directory / "textured" / f"{identifier}.png"),
        "generation_output": {
            "path": f"generation/{identifier}.png",
            "sha256": digest(output_path),
        },
        "prompt": Path(prompt).read_text(),
        "model_version": "not exposed by tool",
        "generation_cost_reported": None,
        "geographic_fidelity_approved": False,
    }
    record_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(record_path, record)
    return record


def assemble(directory, allow_partial=False):
    directory = Path(directory)
    plan = load_plan(directory)
    with Image.open(directory / "inputs/structure.png") as image:
        atlas = image.convert("RGB")
    count = 0
    for identifier, patch in plan["patches"].items():
        path = directory / "styled" / f"{identifier}.png"
        if not path.exists() and allow_partial:
            continue
        record = json.loads((directory / "records" / f"{identifier}.json").read_text())
        if digest(path) != record["styled_sha256"]:
            raise ValueError("Accepted core changed")
        with Image.open(path) as image:
            if image.size != (CORE, CORE):
                raise ValueError("Invalid core size")
            atlas.paste(image, tuple(patch["bounds_px"][:2]))
        count += 1
    clean_png(atlas, directory / "inspection/atlas.png")
    regions = []
    for line in (1, 2):
        for segment in range(3):
            p, start = line * CORE, segment * CORE
            regions += [
                (f"v{line}-r{segment}", [p - 192, start, p + 192, start + CORE]),
                (f"h{line}-c{segment}", [start, p - 192, start + CORE, p + 192]),
            ]
    for x in (1, 2):
        for y in (1, 2):
            regions.append(
                (
                    f"x{x}-y{y}",
                    [x * CORE - 256, y * CORE - 256, x * CORE + 256, y * CORE + 256],
                )
            )
    for name, box in regions:
        clean_png(atlas.crop(box), directory / "inspection" / f"{name}.png")
    return {
        "assembled_cores": count,
        "total_cores": 9,
        "inspection_regions": len(regions),
    }


def review_template(directory):
    """Hash current pixels and leave every visual decision explicitly pending."""
    from build_map_release import JOINS, JUNCTIONS, review_binding

    directory = Path(directory)
    assemble(directory)
    with (
        Image.open(directory / "inspection/atlas.png") as atlas,
        Image.open(directory / "inputs/source.png") as source,
    ):
        binding = review_binding(atlas, source)
    return {
        "seam_review": {
            "status": "pending",
            "note": "Not reviewed.",
            "binding": binding,
            "joins": [
                {"id": name, "passed": False, "note": "Not reviewed."}
                for name in sorted(JOINS)
            ],
            "junctions": [
                {"id": name, "passed": False, "note": "Not reviewed."}
                for name in sorted(JUNCTIONS)
            ],
        },
        "source_comparison": {
            "status": "pending",
            "passed": False,
            "note": "Not reviewed.",
        },
    }


def export(directory, config_path, source_metadata, review_path, prompt_path):
    """Make the release handoff only after a recorded, complete visual review."""
    from atlas import load_config
    from build_map_release import (
        review_binding,
        validate_inputs,
        validate_review,
        validate_source_comparison,
    )

    directory = Path(directory)
    assemble(directory)
    config = load_config(config_path)
    source_metadata = Path(source_metadata)
    source = json.loads(source_metadata.read_text())
    if (
        source.get("status") != "complete"
        or source.get("source_config_sha256") != config.fingerprint
        or source.get("native_resolution_px") != [SIZE, SIZE]
        or source.get("upscaled") is not False
        or source.get("core_reassembly_pixel_exact") is not True
    ):
        raise ValueError("A completed matching native source render is required")
    for source_id, local in (
        ("source-reference", "source"),
        ("atlas-ink", "structure"),
    ):
        entries = [entry for entry in source["outputs"] if entry["id"] == source_id]
        if len(entries) != 1:
            raise ValueError("Native source render is missing an expected frame")
        entry = entries[0]
        path = source_metadata.parent / entry["path"]
        if digest(path) != entry["sha256"]:
            raise ValueError("Native source evidence changed")
        with (
            Image.open(path) as expected,
            Image.open(directory / "inputs" / f"{local}.png") as actual,
        ):
            if expected.convert("RGB").tobytes() != actual.convert("RGB").tobytes():
                raise ValueError("Pilot inputs differ from the native rendered pixels")
    review = json.loads(Path(review_path).read_text())
    if set(review) != {"seam_review", "source_comparison"}:
        raise ValueError("Both seam and source comparison reviews are required")
    validate_review(review["seam_review"])
    validate_source_comparison(review["source_comparison"])
    with (
        Image.open(directory / "inspection/atlas.png") as atlas,
        Image.open(directory / "inputs/source.png") as source_image,
    ):
        if review["seam_review"]["binding"] != review_binding(atlas, source_image):
            raise ValueError(
                "Visual review belongs to different artwork or source pixels"
            )
    index = {
        "schema_version": 1,
        "status": "complete",
        "ai_styling": True,
        "bounds_px": [0, 0, SIZE, SIZE],
        "chunk_size_px": CORE,
        "config_sha256": config.fingerprint,
        "layers": ["styled", "textured"],
        "chunks": [],
    }
    evidence = {
        "schema_version": 1,
        "artwork_method": "ai-assisted",
        "native_artwork": True,
        "upscaled_artwork": False,
        "artwork_approved": False,
        "geographic_fidelity_approved": False,
        "comparison_mode": "reviewed-approximate",
        "comparison_note": "Native source and artwork share the camera framing. Visual comparison is not a surveyed accuracy or pixel registration claim. Facade and small surface details are artistic.",
        "generation_tool": "OpenAI image-generation tool",
        "model_version": "not exposed by tool",
        "prompt": Path(prompt_path).read_text(),
        "patches": [],
        "repairs": [],
        "seam_review": review["seam_review"],
        "source_comparison": review["source_comparison"],
    }
    fields = {
        "id",
        "styled_sha256",
        "source_sha256",
        "generation_output",
        "crop_box_px",
        "source_bounds_px",
        "reference_sha256",
        "prompt",
    }
    for identifier, patch in grid().items():
        record = json.loads((directory / "records" / f"{identifier}.json").read_text())
        evidence["patches"].append({key: record[key] for key in fields})
        outputs = {}
        for layer in index["layers"]:
            path = directory / layer / f"{identifier}.png"
            if layer == "textured":
                with (
                    Image.open(directory / "inputs/source.png") as native,
                    Image.open(path) as core,
                ):
                    if (
                        core.convert("RGB").tobytes()
                        != native.crop(patch["bounds_px"]).convert("RGB").tobytes()
                    ):
                        raise ValueError(
                            "Source core differs from the verified native source crop"
                        )
            outputs[layer] = {
                "path": path.relative_to(directory).as_posix(),
                "width": CORE,
                "height": CORE,
                "bytes": path.stat().st_size,
                "sha256": digest(path),
            }
        index["chunks"].append(
            {"id": identifier, "bounds_px": patch["bounds_px"], "outputs": outputs}
        )
    write_json(directory / "render-index.json", index)
    write_json(directory / "evidence.json", evidence)
    validate_inputs(
        directory / "render-index.json", Path(config_path), directory / "evidence.json"
    )
    return {
        "index": "render-index.json",
        "evidence": "evidence.json",
        "native_cores": 9,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--source", type=Path, required=True)
    init.add_argument("--structure", type=Path, required=True)
    init.add_argument("--style", type=Path, required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--id", choices=ORDER, required=True)
    ingest_parser = commands.add_parser("ingest")
    ingest_parser.add_argument("--id", choices=ORDER, required=True)
    ingest_parser.add_argument("--image", type=Path, required=True)
    ingest_parser.add_argument("--prompt", type=Path, required=True)
    assemble_parser = commands.add_parser("assemble")
    assemble_parser.add_argument("--allow-partial", action="store_true")
    exporter = commands.add_parser("export")
    for option in ("config", "source-metadata", "review", "prompt"):
        exporter.add_argument("--" + option, type=Path, required=True)
    reviewer = commands.add_parser("review-template")
    for child in (
        init,
        prepare_parser,
        ingest_parser,
        assemble_parser,
        exporter,
        reviewer,
    ):
        child.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "init":
        result = initialize(args.directory, args.source, args.structure, args.style)
    elif args.command == "prepare":
        result = prepare(args.directory, args.id)
    elif args.command == "ingest":
        result = ingest(args.directory, args.id, args.image, args.prompt)
    elif args.command == "assemble":
        result = assemble(args.directory, args.allow_partial)
    elif args.command == "review-template":
        result = review_template(args.directory)
    else:
        result = export(
            args.directory, args.config, args.source_metadata, args.review, args.prompt
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

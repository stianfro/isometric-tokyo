"""Bounded native extension of a verified nine-patch map, never an enlargement.

All bounds are global map pixels. PNGs use canvas-local pixels. Parent native
proofs stay byte-identical, and only the sixteen new frontier cells are generated.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import art_pilot as pilot
import build_map_release as release
from PIL import Image

BOUNDS = release.EXPANSION_BOUNDS
CORE = pilot.CORE
WINDOW = pilot.WINDOW
SIZE = 5120
MAX_REPAIRS = 8


def local(box):
    return [
        box[0] - BOUNDS[0],
        box[1] - BOUNDS[1],
        box[2] - BOUNDS[0],
        box[3] - BOUNDS[1],
    ]


def grid():
    patches = {}
    for y in range(-1, 4):
        for x in range(-1, 4):
            left, top = x * CORE, y * CORE
            wx = min(max(left - 115, BOUNDS[0]), BOUNDS[2] - WINDOW)
            wy = min(max(top - 115, BOUNDS[1]), BOUNDS[3] - WINDOW)
            ident = f"x{x}_y{y}"
            patches[ident] = {
                "id": ident,
                "bounds_px": [left, top, left + CORE, top + CORE],
                "source_bounds_px": [wx, wy, wx + WINDOW, wy + WINDOW],
                "crop_box_px": [left - wx, top - wy, left - wx + CORE, top - wy + CORE],
            }
    return patches


def validate_order(order):
    patches = grid()
    accepted = set(pilot.grid())
    if (
        not isinstance(order, list)
        or len(order) != 16
        or set(order) != set(patches) - accepted
    ):
        raise ValueError(
            "Expansion order must contain exactly sixteen unique new cells"
        )
    for ident in order:
        box = patches[ident]["bounds_px"]
        if not any(
            (
                abs(box[0] - patches[prior]["bounds_px"][0]) == CORE
                and box[1] == patches[prior]["bounds_px"][1]
            )
            or (
                abs(box[1] - patches[prior]["bounds_px"][1]) == CORE
                and box[0] == patches[prior]["bounds_px"][0]
            )
            for prior in accepted
        ):
            raise ValueError("Every new cell must share an edge with accepted artwork")
        accepted.add(ident)
    return order


def initialize(directory, source, structure, style, parent_release, order):
    directory, parent_release = Path(directory), Path(parent_release)
    order = validate_order(order)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Expansion requires an empty output directory")
    parent = release.read_json(parent_release / "release.json")
    # Full browsing release verification on import, then compact native proof retention.
    release.verify_version(parent_release, parent["id"])
    index, evidence, files = release.parent_proof(parent_release, parent["id"])
    directory.mkdir(parents=True, exist_ok=True)
    for name, path in files.items():
        target = directory / "parent" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    for path, name, size in (
        (source, "source.png", (SIZE, SIZE)),
        (structure, "structure.png", (SIZE, SIZE)),
        (style, "style.png", None),
    ):
        with Image.open(path) as image:
            if size and image.size != size:
                raise ValueError(
                    "Expansion source requires a native 5120 square render"
                )
            pilot.clean_png(image, directory / "inputs" / name)
    plan = {
        "schema_version": 2,
        "bounds_px": list(BOUNDS),
        "chunk_size_px": CORE,
        "generation_window_px": WINDOW,
        "generation_order": order,
        "max_main_calls": 16,
        "max_repair_calls": MAX_REPAIRS,
        "artwork_approved": False,
        "geographic_fidelity_approved": False,
        "patches": grid(),
        "parent": {"id": parent["id"], "release_path": "parent/release.json"},
        "parent_files": {
            name: release.digest(directory / "parent" / name) for name in files
        },
        "inputs": {
            name: release.digest(directory / "inputs" / name)
            for name in ("source.png", "structure.png", "style.png")
        },
    }
    pilot.write_json(directory / "plan.json", plan)
    for chunk in index["chunks"]:
        path = directory / "styled" / f"{chunk['id']}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(parent_release / chunk["outputs"]["styled"]["path"], path)
    with Image.open(directory / "inputs/source.png") as source_image:
        for ident, patch in grid().items():
            with source_image.crop(local(patch["bounds_px"])) as core:
                pilot.clean_png(core, directory / "textured" / f"{ident}.png")
    return plan


def load_plan(directory):
    directory = Path(directory)
    plan = release.read_json(directory / "plan.json")
    if (
        plan.get("schema_version") != 2
        or plan.get("bounds_px") != list(BOUNDS)
        or plan.get("chunk_size_px") != CORE
        or plan.get("generation_window_px") != WINDOW
        or plan.get("max_main_calls") != 16
        or plan.get("max_repair_calls") != MAX_REPAIRS
        or plan.get("artwork_approved") is not False
        or plan.get("geographic_fidelity_approved") is not False
        or plan.get("patches") != grid()
        or set(plan.get("inputs", {})) != {"source.png", "structure.png", "style.png"}
    ):
        raise ValueError("Expansion plan differs from the bounded native grid")
    validate_order(plan["generation_order"])
    for name, checksum in plan["inputs"].items():
        if release.digest(release.safe_file(directory / "inputs", name)) != checksum:
            raise ValueError("Expansion source or style pixels changed")
    if plan.get("parent", {}).get("release_path") != "parent/release.json":
        raise ValueError("Expansion parent reference changed")
    # The complete immutable parent identity and exact retained files are checked on export.
    # Every command checks the frozen retained-file inventory, including that identity.
    if not isinstance(plan.get("parent_files"), dict) or not plan["parent_files"]:
        raise ValueError("Parent proof inventory is missing")
    parent_paths = list((directory / "parent").rglob("*"))
    if any(path.is_symlink() for path in parent_paths):
        raise ValueError("Symlink parent proofs are forbidden")
    actual = {
        path.relative_to(directory / "parent").as_posix(): release.digest(path)
        for path in parent_paths
        if path.is_file()
    }
    if actual != plan["parent_files"]:
        raise ValueError("Retained parent native proof changed")
    parent = release.read_json(directory / "parent/release.json")
    if (
        parent.get("id") != plan["parent"]["id"]
        or release.release_id(parent["identity"]) != plan["parent"]["id"]
    ):
        raise ValueError("Parent immutable release identity changed")
    originals = {item["path"]: item["sha256"] for item in parent["identity"]["assets"]}
    if any(
        name != "release.json" and originals.get(name) != checksum
        for name, checksum in actual.items()
    ):
        raise ValueError(
            "Parent proof no longer matches its original immutable inventory"
        )
    return plan


def repair_geometry(ident, bounds, window):
    import re

    bounds, window = release.rectangle(bounds), release.rectangle(window)
    if (
        not isinstance(ident, str)
        or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", ident)
        or window[0] < BOUNDS[0]
        or window[1] < BOUNDS[1]
        or window[2] > BOUNDS[2]
        or window[3] > BOUNDS[3]
        or window[2] - window[0] != WINDOW
        or window[3] - window[1] != WINDOW
        or pilot.intersection(bounds, window) != bounds
        or (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]) > CORE * CORE
    ):
        raise ValueError("Repair must be a bounded exact crop of a native 1254 window")
    release.validate_parent_frontier_repair(bounds)
    return {
        "id": ident,
        "bounds_px": bounds,
        "source_bounds_px": window,
        "crop_box_px": [
            bounds[0] - window[0],
            bounds[1] - window[1],
            bounds[2] - window[0],
            bounds[3] - window[1],
        ],
    }


def repair_records(directory):
    path = directory / "repairs/index.json"
    if not path.exists():
        return []
    index = release.read_json(path)
    ids = index.get("repairs")
    if (
        set(index) != {"schema_version", "repairs"}
        or index["schema_version"] != 1
        or not isinstance(ids, list)
        or len(ids) > MAX_REPAIRS
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("Expected at most eight distinct ordered expansion repairs")
    records = []
    for ident in ids:
        record = release.read_json(
            release.safe_file(directory / "repairs", f"{ident}.json")
        )
        geometry = repair_geometry(
            ident, record["bounds_px"], record["source_bounds_px"]
        )
        if any(record.get(key) != value for key, value in geometry.items()):
            raise ValueError("Expansion repair geometry changed")
        records.append(record)
    return records


def verify_repair_reference(directory, record, atlas):
    if record["atlas_before_rgb_sha256"] != release.rgb_digest(atlas):
        raise ValueError("Repair reference belongs to different artwork")
    paths = [
        directory / "inputs" / f"repair-{record['id']}-{suffix}.png"
        for suffix in ("context", "source")
    ]
    if record["reference_sha256"] != [
        *(release.digest(path) for path in paths),
        release.digest(directory / "inputs/style.png"),
    ]:
        raise ValueError("Repair reference changed after preparation")
    with (
        Image.open(paths[0]) as context,
        Image.open(paths[1]) as source,
        Image.open(directory / "inputs/source.png") as full_source,
    ):
        box = local(record["source_bounds_px"])
        with (
            atlas.crop(box) as expected_context,
            full_source.crop(box) as expected_source,
        ):
            if (
                context.size != (WINDOW, WINDOW)
                or source.size != (WINDOW, WINDOW)
                or context.convert("RGB").tobytes() != expected_context.tobytes()
                or source.convert("RGB").tobytes() != expected_source.tobytes()
            ):
                raise ValueError(
                    "Repair references differ from exact native frame crops"
                )


def expected_atlas(directory, allow_partial=False):
    directory = Path(directory)
    plan = load_plan(directory)
    with Image.open(directory / "inputs/structure.png") as structure:
        atlas = structure.convert("RGB")
    count = 0
    try:
        parent_index = release.read_json(directory / "parent/source-index.json")
        for chunk in parent_index["chunks"]:
            with release.image(
                directory / "parent" / chunk["outputs"]["styled"]["path"]
            ) as pixels:
                atlas.paste(pixels, tuple(local(chunk["bounds_px"])[:2]))
                count += 1
        missing = False
        for ident in plan["generation_order"]:
            path = directory / "records" / f"{ident}.json"
            if not path.exists():
                if not allow_partial:
                    raise ValueError(
                        "Every expansion cell must be generated before review"
                    )
                missing = True
                continue
            if missing:
                raise ValueError(
                    "Accepted cells do not follow the recorded frontier order"
                )
            record = release.read_json(path)
            geometry = plan["patches"][ident]
            if any(record.get(key) != value for key, value in geometry.items()):
                raise ValueError("Accepted expansion geometry changed")
            _, pixels = release.generation(record, directory, geometry["bounds_px"])
            with pixels:
                atlas.paste(pixels, tuple(local(geometry["bounds_px"])[:2]))
            count += 1
        repairs = repair_records(directory)
        if allow_partial and repairs:
            raise ValueError("Repaired expansions require complete assembly")
        for record in repairs:
            verify_repair_reference(directory, record, atlas)
            _, pixels = release.generation(record, directory, record["bounds_px"])
            with pixels:
                atlas.paste(pixels, tuple(local(record["bounds_px"])[:2]))
        return atlas, count
    except BaseException:
        atlas.close()
        raise


def prepare(directory, ident):
    directory = Path(directory)
    plan = load_plan(directory)
    if ident not in plan["generation_order"]:
        raise ValueError("Only new expansion cells can be generated")
    if (directory / "records" / f"{ident}.json").exists():
        raise ValueError("Accepted expansion artwork is immutable")
    geometry = plan["patches"][ident]
    order = plan["generation_order"]
    priors = list(pilot.grid()) + order[: order.index(ident)]
    for prior in order[: order.index(ident)]:
        if not (directory / "records" / f"{prior}.json").exists():
            raise ValueError("Generate earlier frontier cells first")
    atlas, _ = expected_atlas(directory, allow_partial=True)
    anchors = []
    try:
        with atlas.crop(local(geometry["source_bounds_px"])) as context:
            for prior in priors:
                box = plan["patches"][prior]["bounds_px"]
                overlap = pilot.intersection(box, geometry["source_bounds_px"])
                path = directory / "styled" / f"{prior}.png"
                with Image.open(path) as actual, atlas.crop(local(box)) as expected:
                    if actual.convert("RGB").tobytes() != expected.tobytes():
                        raise ValueError("Accepted context pixels changed")
                if overlap:
                    window = geometry["source_bounds_px"]
                    anchors.append(
                        {
                            "id": prior,
                            "bounds_px": overlap,
                            "local_bounds_px": [
                                overlap[0] - window[0],
                                overlap[1] - window[1],
                                overlap[2] - window[0],
                                overlap[3] - window[1],
                            ],
                            "sha256": release.digest(path),
                        }
                    )
            pilot.clean_png(context, directory / "inputs" / f"{ident}-context.png")
    finally:
        atlas.close()
    with (
        Image.open(directory / "inputs/source.png") as source,
        source.crop(local(geometry["source_bounds_px"])) as crop,
    ):
        pilot.clean_png(crop, directory / "inputs" / f"{ident}-source.png")
    reference = {
        **geometry,
        "anchors": anchors,
        "reference_sha256": [
            release.digest(directory / "inputs" / f"{ident}-{suffix}.png")
            for suffix in ("context", "source")
        ]
        + [plan["inputs"]["style.png"]],
    }
    pilot.write_json(directory / "inputs" / f"{ident}.json", reference)
    return reference


def ingest(directory, ident, generated, prompt):
    directory = Path(directory)
    plan = load_plan(directory)
    if ident not in plan["generation_order"]:
        raise ValueError("Parent cores cannot be regenerated")
    record_path = directory / "records" / f"{ident}.json"
    if record_path.exists():
        raise ValueError("Accepted native expansion outputs cannot be overwritten")
    reference = release.read_json(directory / "inputs" / f"{ident}.json")
    if any(
        reference.get(key) != value for key, value in plan["patches"][ident].items()
    ):
        raise ValueError("Prepared expansion geometry changed")
    for suffix, checksum in zip(
        ("context", "source"), reference["reference_sha256"][:2], strict=True
    ):
        if release.digest(directory / "inputs" / f"{ident}-{suffix}.png") != checksum:
            raise ValueError("Expansion generation reference changed")
    if reference["reference_sha256"][2:] != [plan["inputs"]["style.png"]]:
        raise ValueError("Expansion style reference changed")
    atlas, _ = expected_atlas(directory, allow_partial=True)
    try:
        with (
            Image.open(directory / "inputs" / f"{ident}-context.png") as context,
            atlas.crop(local(reference["source_bounds_px"])) as expected,
        ):
            if context.convert("RGB").tobytes() != expected.tobytes():
                raise ValueError(
                    "Prepared context differs from accepted native neighbours"
                )
    finally:
        atlas.close()
    with (
        Image.open(directory / "inputs/source.png") as source,
        source.crop(local(reference["source_bounds_px"])) as expected,
        Image.open(directory / "inputs" / f"{ident}-source.png") as actual,
    ):
        if expected.tobytes() != actual.convert("RGB").tobytes():
            raise ValueError("Prepared reference differs from native source crop")
    prompt_text = release.text(Path(prompt).read_text())
    output, core = (
        directory / "generation" / f"{ident}.png",
        directory / "styled" / f"{ident}.png",
    )
    with Image.open(generated) as image:
        if image.size != (WINDOW, WINDOW):
            raise ValueError("Expected native 1254 square output, no resizing allowed")
        pilot.clean_png(image, output)
        with image.crop(reference["crop_box_px"]) as crop:
            pilot.clean_png(crop, core)
    record = {
        **reference,
        "styled_sha256": release.digest(core),
        "source_sha256": release.digest(directory / "textured" / f"{ident}.png"),
        "generation_output": {
            "path": output.relative_to(directory).as_posix(),
            "sha256": release.digest(output),
        },
        "prompt": prompt_text,
        "model_version": "not exposed by tool",
        "generation_cost_reported": None,
        "geographic_fidelity_approved": False,
    }
    record_path.parent.mkdir(parents=True, exist_ok=True)
    pilot.write_json(record_path, record)
    return record


def assemble(directory, allow_partial=False):
    directory = Path(directory)
    atlas, count = expected_atlas(directory, allow_partial)
    try:
        for ident, patch in grid().items():
            path = directory / "styled" / f"{ident}.png"
            if not path.exists() and allow_partial:
                continue
            with (
                Image.open(path) as pixels,
                atlas.crop(local(patch["bounds_px"])) as expected,
            ):
                if (
                    pixels.size != (CORE, CORE)
                    or pixels.convert("RGB").tobytes() != expected.tobytes()
                ):
                    raise ValueError(
                        "Expansion core differs from parent, native crop or recorded repairs"
                    )
        pilot.clean_png(atlas, directory / "inspection/atlas.png")
        for ident, box in release.review_regions(BOUNDS).items():
            with atlas.crop(box) as crop:
                pilot.clean_png(crop, directory / "inspection" / f"{ident}.png")
    finally:
        atlas.close()
    return {"assembled_cores": count, "total_cores": 25, "inspection_regions": 56}


def review_template(directory):
    directory = Path(directory)
    assemble(directory)
    with (
        Image.open(directory / "inspection/atlas.png") as atlas,
        Image.open(directory / "inputs/source.png") as source,
    ):
        binding = release.review_binding(atlas, source, BOUNDS)
    previous = release.read_json(directory / "parent/evidence.json")["seam_review"]
    prior_records = {
        record["id"]: record for record in previous["joins"] + previous["junctions"]
    }
    records = {}
    for ident, checksum in binding["regions_rgb_sha256"].items():
        if previous["binding"]["regions_rgb_sha256"].get(ident) == checksum:
            prior = prior_records[ident]
            records[ident] = {
                "id": ident,
                "passed": prior["passed"],
                "note": "Carried from immutable parent review because this region's RGB pixels are identical. "
                + prior["note"],
            }
        else:
            records[ident] = {"id": ident, "passed": False, "note": "Not reviewed."}
    return {
        "seam_review": {
            "status": "pending",
            "note": "New and changed regions require visual review.",
            "binding": binding,
            "joins": [
                records[name] for name in sorted(records) if name.startswith(("v", "h"))
            ],
            "junctions": [
                records[name] for name in sorted(records) if name.startswith("x")
            ],
        },
        "source_comparison": {
            "status": "pending",
            "passed": False,
            "note": "Expanded native source requires a new visual comparison.",
        },
    }


def prepare_repair(directory, ident, bounds, window):
    directory = Path(directory)
    geometry = repair_geometry(ident, bounds, window)
    records = repair_records(directory)
    if len(records) >= MAX_REPAIRS:
        raise ValueError("At most eight new expansion repairs are allowed")
    path = directory / "inputs" / f"repair-{ident}.json"
    if path.exists() or any(record["id"] == ident for record in records):
        raise ValueError("Prepared and accepted repair references are immutable")
    assemble(directory)
    paths = []
    for source, suffix in (
        ("inspection/atlas.png", "context"),
        ("inputs/source.png", "source"),
    ):
        target = directory / "inputs" / f"repair-{ident}-{suffix}.png"
        with Image.open(directory / source) as image, image.crop(local(window)) as crop:
            pilot.clean_png(crop, target)
            if suffix == "context":
                before = release.rgb_digest(image)
        paths.append(target)
    reference = {
        **geometry,
        "atlas_before_rgb_sha256": before,
        "reference_sha256": [
            *(release.digest(path) for path in paths),
            release.digest(directory / "inputs/style.png"),
        ],
    }
    pilot.write_json(path, reference)
    return reference


def ingest_repair(directory, ident, generated, prompt):
    directory = Path(directory)
    reference = release.read_json(
        release.safe_file(directory / "inputs", f"repair-{ident}.json")
    )
    geometry = repair_geometry(
        ident, reference["bounds_px"], reference["source_bounds_px"]
    )
    if any(reference.get(key) != value for key, value in geometry.items()):
        raise ValueError("Prepared repair geometry changed")
    records = repair_records(directory)
    if len(records) >= MAX_REPAIRS or any(record["id"] == ident for record in records):
        raise ValueError("Expansion repairs are bounded and immutable")
    record_path = directory / "repairs" / f"{ident}.json"
    output = directory / "generation" / f"repair-{ident}.png"
    prior_review = directory / "repairs" / f"{ident}-prior-review.json"
    if any(path.exists() for path in (record_path, output, prior_review)):
        raise ValueError("Accepted expansion repairs cannot be overwritten")
    assemble(directory)
    prompt_text = release.text(Path(prompt).read_text())
    with Image.open(directory / "inspection/atlas.png") as input_image:
        atlas = input_image.convert("RGB")
    try:
        verify_repair_reference(directory, reference, atlas)
        with Image.open(generated) as image:
            if image.size != (WINDOW, WINDOW):
                raise ValueError("Expected native 1254 square repair output")
            native = image.convert("RGB")
            with native.crop(reference["crop_box_px"]) as crop:
                atlas.paste(crop, tuple(local(reference["bounds_px"])[:2]))
        affected = {
            directory / "styled" / f"{name}.png": patch["bounds_px"]
            for name, patch in grid().items()
            if pilot.intersection(reference["bounds_px"], patch["bounds_px"])
        }
        index_path, review_path = (
            directory / "repairs/index.json",
            directory / "review.json",
        )
        backups = {
            path: path.read_bytes() if path.exists() else None
            for path in (*affected, index_path, review_path)
        }
        try:
            record_path.parent.mkdir(parents=True, exist_ok=True)
            pilot.clean_png(native, output)
            record = {
                **reference,
                "generation_output": {
                    "path": output.relative_to(directory).as_posix(),
                    "sha256": release.digest(output),
                },
                "prompt": prompt_text,
            }
            pilot.write_json(record_path, record)
            pilot.write_json(
                index_path,
                {
                    "schema_version": 1,
                    "repairs": [*(record["id"] for record in records), ident],
                },
            )
            for path, box in affected.items():
                with atlas.crop(local(box)) as crop:
                    pilot.clean_png(crop, path)
            if backups[review_path] is not None:
                prior_review.write_bytes(backups[review_path])
            pilot.write_json(review_path, review_template(directory))
        except BaseException:
            for path, content in backups.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(content)
            for path in (record_path, output, prior_review):
                path.unlink(missing_ok=True)
            raise
        finally:
            native.close()
    finally:
        atlas.close()
    return {
        "id": ident,
        "bounds_px": reference["bounds_px"],
        "crop_box_px": reference["crop_box_px"],
        "repair_count": len(records) + 1,
        "review_status": "pending",
    }


def export(directory, config_path, source_metadata, review_path, prompt_path):
    from atlas import load_config

    directory = Path(directory)
    plan = load_plan(directory)
    assemble(directory)
    config = load_config(config_path)
    source_metadata = Path(source_metadata)
    source = release.read_json(source_metadata)
    if (
        source.get("status") != "complete"
        or source.get("source_config_sha256") != config.fingerprint
        or source.get("native_resolution_px") != [SIZE, SIZE]
        or source.get("upscaled") is not False
        or source.get("core_reassembly_pixel_exact") is not True
    ):
        raise ValueError("A completed matching native expansion source is required")
    for ident, name in (("source-reference", "source"), ("atlas-ink", "structure")):
        entries = [entry for entry in source["outputs"] if entry["id"] == ident]
        if len(entries) != 1:
            raise ValueError("Native source evidence must contain both frames")
        entry = entries[0]
        path = release.safe_file(source_metadata.parent, entry["path"])
        if release.digest(path) != entry["sha256"]:
            raise ValueError("Native expansion source evidence changed")
        with (
            Image.open(path) as expected,
            Image.open(directory / "inputs" / f"{name}.png") as actual,
        ):
            if expected.size != actual.size or release.rgb_digest(
                expected.convert("RGB")
            ) != release.rgb_digest(actual.convert("RGB")):
                raise ValueError("Expansion source differs from native rendered pixels")
    review = release.read_json(Path(review_path))
    if set(review) != {"seam_review", "source_comparison"}:
        raise ValueError("Both expansion visual reviews are required")
    release.validate_review(review["seam_review"], BOUNDS)
    release.validate_source_comparison(review["source_comparison"])
    with (
        Image.open(directory / "inspection/atlas.png") as atlas,
        Image.open(directory / "inputs/source.png") as source_image,
    ):
        if review["seam_review"]["binding"] != release.review_binding(
            atlas, source_image, BOUNDS
        ):
            raise ValueError("Expansion review belongs to different final pixels")
    index = {
        "schema_version": 1,
        "status": "complete",
        "ai_styling": True,
        "bounds_px": list(BOUNDS),
        "chunk_size_px": CORE,
        "config_sha256": config.fingerprint,
        "layers": ["styled", "textured"],
        "chunks": [],
    }
    parent_evidence = release.read_json(directory / "parent/evidence.json")
    parent_patches = {record["id"]: record for record in parent_evidence["patches"]}
    historical = json.loads(json.dumps(parent_evidence["repairs"]))
    for record in historical:
        record["generation_output"]["path"] = (
            "parent/" + record["generation_output"]["path"]
        )
    repair_fields = (
        "id",
        "bounds_px",
        "generation_output",
        "crop_box_px",
        "source_bounds_px",
        "reference_sha256",
        "prompt",
    )
    evidence = {
        **{
            key: value
            for key, value in parent_evidence.items()
            if key not in {"patches", "repairs", "seam_review", "source_comparison"}
        },
        "schema_version": 2,
        "source_provenance": source.get("source_provenance"),
        "parent": plan["parent"],
        "prompt": release.text(Path(prompt_path).read_text()),
        "comparison_mode": "reviewed-approximate",
        "comparison_note": "Native source and artwork share fixed world framing, not pixel registration or surveyed accuracy. Major building massing and routes received visual comparison. Facades, roofs, planting and park paths remain artistic interpretations. The nine parent artwork cores are retained except for explicitly recorded border repairs. This bounded 25-patch expansion is not complete district coverage or a navigation map.",
        "patches": [],
        "repairs": historical
        + [
            {key: record[key] for key in repair_fields}
            for record in repair_records(directory)
        ],
        **review,
    }
    fields = (
        "id",
        "generation_output",
        "crop_box_px",
        "source_bounds_px",
        "reference_sha256",
        "prompt",
    )
    with Image.open(directory / "inputs/source.png") as native:
        for ident, geometry in grid().items():
            record = (
                json.loads(json.dumps(parent_patches[ident]))
                if ident in parent_patches
                else release.read_json(directory / "records" / f"{ident}.json")
            )
            if ident in parent_patches:
                record["generation_output"]["path"] = (
                    "parent/" + record["generation_output"]["path"]
                )
            outputs = {}
            for layer in index["layers"]:
                path = directory / layer / f"{ident}.png"
                if layer == "textured":
                    with (
                        Image.open(path) as core,
                        native.crop(local(geometry["bounds_px"])) as expected,
                    ):
                        if core.convert("RGB").tobytes() != expected.tobytes():
                            raise ValueError(
                                "Expansion source core differs from native source crop"
                            )
                outputs[layer] = {
                    "path": path.relative_to(directory).as_posix(),
                    "width": CORE,
                    "height": CORE,
                    "bytes": path.stat().st_size,
                    "sha256": release.digest(path),
                }
            evidence["patches"].append(
                {
                    **{key: record[key] for key in fields if key in record},
                    "styled_sha256": outputs["styled"]["sha256"],
                    "source_sha256": outputs["textured"]["sha256"],
                }
            )
            index["chunks"].append(
                {"id": ident, "bounds_px": geometry["bounds_px"], "outputs": outputs}
            )
    pilot.write_json(directory / "render-index.json", index)
    pilot.write_json(directory / "evidence.json", evidence)
    release.validate_inputs(
        directory / "render-index.json", Path(config_path), directory / "evidence.json"
    )
    return {
        "index": "render-index.json",
        "evidence": "evidence.json",
        "native_cores": 25,
        "inherited_cores": 9,
        "new_native_cores": 16,
    }

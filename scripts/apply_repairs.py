"""Build an immutable study chunk release from explicitly approved repair jobs."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import tempfile
from pathlib import Path

from atlas import validate_bounds
from chunk_pyramid import NAME, digest, intersect, read_index, safe_path
from jobs import Ledger, artifact_path
from PIL import Image
from review_regions import (
    EDIT_METHODS,
    MAX_INDEX_BYTES,
    REPAIRABLE_LAYERS,
    bounded_size,
    clean_png,
    content_id,
    verify_bundle,
    verify_source_jobs,
    write_json,
)

MAX_CHUNKS = 256
MAX_REPAIRS = 32
MAX_BYTES = 512_000_000
MAX_JOBS = 4096


def checked_asset(root, record):
    path = safe_path(root, record["path"])
    if (
        not path.is_file()
        or type(record["bytes"]) is not int
        or not 0 <= record["bytes"] <= MAX_BYTES
        or path.stat().st_size != record["bytes"]
        or digest(path) != record["sha256"]
    ):
        raise ValueError("Asset checksum or byte count mismatch")
    return path


def approved_closure(ledger, identifiers, workspace):
    """Recheck every transitive approved dependency, including its artifacts."""
    pending, found, seen_files = list(identifiers), {}, set()
    total = 0
    while pending:
        ident = pending.pop()
        if ident in found:
            continue
        if len(found) >= MAX_JOBS:
            raise ValueError("Repair dependency closure exceeds study limit")
        job = ledger.get(ident)
        if job["state"] != "approved":
            raise ValueError("Every repair and transitive dependency must be approved")
        for record in job["artifacts"]:
            name = record["path"]
            if name not in seen_files:
                if type(record["bytes"]) is not int or record["bytes"] < 0:
                    raise ValueError("Invalid dependency artifact byte count")
                total += record["bytes"]
                if total > MAX_BYTES:
                    raise ValueError("Dependency artifacts exceed bounded study limit")
                seen_files.add(name)
            checked_asset(workspace, record)
        deps = sorted(
            row[0]
            for row in ledger.db.execute(
                "SELECT dependency FROM dependencies WHERE job=?", (ident,)
            )
        )
        found[ident] = {"job": job, "dependencies": deps}
        pending.extend(deps)
    return found


def load_repairs(index_path, index, chunks, identifiers, ledger, workspace):
    if (
        not identifiers
        or len(identifiers) > MAX_REPAIRS
        or len(identifiers) != len(set(identifiers))
    ):
        raise ValueError("Select between one and 32 unique approved repair jobs")
    closure = approved_closure(ledger, identifiers, workspace)
    repairs = []
    index_hash = digest(index_path)
    for ident in sorted(identifiers):
        job = closure[ident]["job"]
        if job["kind"] != "manual-repair":
            raise ValueError("Only manual-repair jobs can be applied")
        manifests = [
            record
            for record in job["artifacts"]
            if Path(record["path"]).name == "manifest.json"
        ]
        if len(manifests) != 1:
            raise ValueError("Repair job needs exactly one immutable bundle manifest")
        manifest_path = artifact_path(workspace, manifests[0]["path"])
        repair = verify_bundle(manifest_path.parent, "manual-repair")
        payload = job["payload"]
        if (
            not isinstance(repair.get("edit_method"), str)
            or repair["edit_method"] not in EDIT_METHODS
        ):
            raise ValueError(
                "Repair edit method is unknown; explicit provenance required"
            )
        for key, expected in (
            ("repair_id", repair["id"]),
            ("edit_method", repair["edit_method"]),
            ("bounds_px", repair["bounds_px"]),
            ("layer", repair["layer"]),
            ("config_sha256", repair["config_sha256"]),
            ("source_region_id", repair["source_region_id"]),
        ):
            if payload.get(key) != expected:
                raise ValueError("Repair job payload disagrees with immutable manifest")
        expected_paths = {
            (manifest_path.parent / record["path"]).relative_to(workspace).as_posix()
            for record in repair["files"].values()
        } | {manifest_path.relative_to(workspace).as_posix()}
        if {record["path"] for record in job["artifacts"]} != expected_paths:
            raise ValueError("Repair job artifact inventory differs from its bundle")
        layer = repair["layer"]
        bounds = list(validate_bounds(repair["bounds_px"]))
        size = bounded_size(bounds)
        if (
            repair["config_sha256"] != index["config_sha256"]
            or layer not in REPAIRABLE_LAYERS
            or layer not in index["layers"]
        ):
            raise ValueError(
                "Repair configuration or artwork layer does not match source"
            )
        if intersect(bounds, index["bounds_px"]) != tuple(bounds):
            raise ValueError("Repair bounds escape source atlas")
        if sorted(repair["source_jobs"]) != closure[ident]["dependencies"]:
            raise ValueError("Repair source jobs do not match ledger dependency edges")
        expected_chunks = {
            chunk["id"]: chunk
            for chunk in chunks
            if intersect(bounds, chunk["bounds_px"])
        }
        sources = repair["source_chunks"]
        if len(sources) != len(expected_chunks) or {
            source["id"] for source in sources
        } != set(expected_chunks):
            raise ValueError(
                "Repair must identify every intersecting source chunk exactly"
            )
        covered = 0
        for source in sources:
            chunk = expected_chunks[source["id"]]
            box = intersect(bounds, chunk["bounds_px"])
            covered += (box[2] - box[0]) * (box[3] - box[1])
            expected = chunk["outputs"][layer]
            expected_record = {
                "path": expected["path"].resolve().relative_to(workspace).as_posix(),
                "sha256": expected["sha256"],
                "bytes": expected["path"].stat().st_size,
            }
            if (
                source["bounds_px"] != chunk["bounds_px"]
                or source["intersection_px"] != list(box)
                or source["outputs"].get(layer) != expected_record
            ):
                raise ValueError(
                    "Repair source chunk identity disagrees with current index"
                )
        if covered != size[0] * size[1]:
            raise ValueError("Repair cannot invent missing sparse source coverage")
        region = {
            "source_chunks": sources,
            "config_sha256": index["config_sha256"],
            "source_index": {"sha256": index_hash},
        }
        verify_source_jobs(ledger, region, layer, repair["source_jobs"], workspace)
        # The preserved source image must really be the pixels being replaced.
        with Image.open(
            manifest_path.parent / repair["files"]["source"]["path"]
        ) as original:
            for chunk in expected_chunks.values():
                box = intersect(bounds, chunk["bounds_px"])
                a = original.crop(
                    (
                        box[0] - bounds[0],
                        box[1] - bounds[1],
                        box[2] - bounds[0],
                        box[3] - bounds[1],
                    )
                )
                with Image.open(chunk["outputs"][layer]["path"]) as image:
                    rgba = image.convert("RGBA")
                    b = rgba.crop(
                        (
                            box[0] - chunk["bounds_px"][0],
                            box[1] - chunk["bounds_px"][1],
                            box[2] - chunk["bounds_px"][0],
                            box[3] - chunk["bounds_px"][1],
                        )
                    )
                    rgba.close()
                matches = a.tobytes() == b.tobytes()
                a.close()
                b.close()
                if not matches:
                    raise ValueError(
                        "Repair preserved source pixels disagree with source chunks"
                    )
        for previous in repairs:
            if intersect(previous["manifest"]["bounds_px"], bounds):
                raise ValueError("Overlapping repair rectangles are not supported")
        repairs.append(
            {
                "job_id": ident,
                "directory": manifest_path.parent,
                "manifest": repair,
                "manifest_sha256": digest(manifest_path),
            }
        )
    return repairs


def source_assets(index_path, index):
    """List bounded source core layers, guides and registry, not stale overscan."""
    records = []
    for chunk in index["chunks"]:
        width = chunk["bounds_px"][2] - chunk["bounds_px"][0]
        height = chunk["bounds_px"][3] - chunk["bounds_px"][1]
        for category in ("outputs", "guides"):
            for name, record in chunk.get(category, {}).items():
                if not NAME.fullmatch(name):
                    raise ValueError("Unsafe source layer or guide name")
                if (record["width"], record["height"]) != (width, height):
                    raise ValueError(
                        "Source guide or layer dimensions disagree with bounds"
                    )
                if "dtype" in record:
                    if (
                        record["dtype"] != "float32-little-endian"
                        or record.get("components") not in (1, 3)
                        or record["bytes"] != width * height * record["components"] * 4
                    ):
                        raise ValueError("Unsupported guide binary encoding")
                records.append(record)
    metadata = index.get("guide_metadata")
    if metadata:
        feature = metadata["feature_id"]
        path = safe_path(index_path.parent, feature["registry"])
        records.append(
            {
                "path": feature["registry"],
                "sha256": feature["registry_sha256"],
                "bytes": path.stat().st_size,
            }
        )
    if (
        any(
            type(record["bytes"]) is not int or record["bytes"] < 0
            for record in records
        )
        or sum(record["bytes"] for record in records) > MAX_BYTES
    ):
        raise ValueError("Source assets exceed bounded study byte limit")
    for record in records:
        checked_asset(index_path.parent, record)
    return records


def verify_release(directory, *, expected_identity=None):
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError("Repair release cannot be a symlink")
    path = directory / "render-index.json"
    if path.stat().st_size > MAX_INDEX_BYTES:
        raise ValueError("Repair index exceeds metadata limit")
    index = json.loads(path.read_text())
    ident = index.get("repair_release_sha256")
    unsigned = {
        key: value for key, value in index.items() if key != "repair_release_sha256"
    }
    if (
        (expected_identity if expected_identity is not None else directory.name)
        != ident
        or content_id(unsigned) != ident
        or index.get("kind") != "repaired-chunk-release"
    ):
        raise ValueError("Repaired release identity mismatch")
    expected = {"render-index.json"}
    for record in index["release_files"]:
        if record["path"] in expected:
            raise ValueError("Duplicate repair release artifact")
        expected.add(record["path"])
        checked_asset(directory, record)
    all_paths = list(directory.rglob("*"))
    if (
        any(path.is_symlink() for path in all_paths)
        or {
            path.relative_to(directory).as_posix()
            for path in all_paths
            if path.is_file()
        }
        != expected
    ):
        raise ValueError("Unexpected or symlinked repair release artifacts")
    if (
        index.get("artwork_approved") is not False
        or index.get("source_coverage_verified") is not False
        or index.get("guides_modified") is not False
        or index.get("release_review_required") is not True
    ):
        raise ValueError("Repair release cannot claim artwork or source approval")
    if type(index.get("source_ai_styling")) is not bool:
        raise ValueError("Repair release lacks source AI provenance")
    expected_ai_styling = index["source_ai_styling"]
    for record in index["repairs"]:
        manifest_path = safe_path(directory, record["manifest_path"])
        repair = verify_bundle(manifest_path.parent, "manual-repair")
        if (
            not isinstance(repair.get("edit_method"), str)
            or repair["edit_method"] not in EDIT_METHODS
        ):
            raise ValueError("Repair edit method is unknown")
        expected_ai_styling = (
            expected_ai_styling or repair["edit_method"] == "ai-assisted"
        )
        if (
            digest(manifest_path) != record["manifest_sha256"]
            or repair["id"] != record["repair_id"]
            or repair["bounds_px"] != record["bounds_px"]
            or repair["layer"] != record["layer"]
        ):
            raise ValueError("Repair proof disagrees with release provenance")
    if (
        type(index.get("ai_styling")) is not bool
        or index["ai_styling"] != expected_ai_styling
    ):
        raise ValueError("Repair release AI provenance disagrees with its inputs")
    read_index(path)
    source_assets(path, index)
    return index


def apply_repairs(index_path, database, identifiers, output, *, workspace=Path(".")):
    workspace = Path(workspace).resolve()
    index_path = Path(index_path).resolve()
    output = Path(output).resolve()
    if not index_path.is_relative_to(workspace) or not output.is_relative_to(workspace):
        raise ValueError("Repair input and output must remain inside workspace")
    if index_path.stat().st_size > MAX_INDEX_BYTES:
        raise ValueError("Source index exceeds metadata limit")
    raw = json.loads(index_path.read_text())
    if type(raw.get("ai_styling")) is not bool:
        raise ValueError("Source index requires explicit boolean ai_styling provenance")
    if (
        not isinstance(raw.get("chunks"), list)
        or not 1 <= len(raw["chunks"]) <= MAX_CHUNKS
    ):
        raise ValueError("Source chunk count exceeds bounded study limit")
    records = source_assets(index_path, raw)
    index_hash = digest(index_path)
    index, chunks, _cells = read_index(index_path)
    if output == index_path.parent or index_path.is_relative_to(output):
        raise ValueError("Output store must not contain the source index")
    ledger = Ledger(database)
    staging = None
    try:
        repairs = load_repairs(
            index_path, index, chunks, identifiers, ledger, workspace
        )
        if any(output.is_relative_to(repair["directory"]) for repair in repairs) or (
            index.get("kind") == "repaired-chunk-release"
            and output.is_relative_to(index_path.parent)
        ):
            raise ValueError("Output cannot modify an immutable source bundle")
        proof_bytes = sum(
            path.stat().st_size
            for repair in repairs
            for path in repair["directory"].iterdir()
        )
        if sum(record["bytes"] for record in records) + proof_bytes > MAX_BYTES:
            raise ValueError("Combined source and proof assets exceed study byte limit")
        output.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".apply-repairs-", dir=output))
        inventory = []

        def copy_record(source, relative, expected):
            destination = safe_path(staging, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            record = {
                "path": relative,
                "sha256": digest(destination),
                "bytes": destination.stat().st_size,
            }
            if (
                record["sha256"] != expected["sha256"]
                or record["bytes"] != expected["bytes"]
            ):
                raise ValueError("Source changed while copying immutable release")
            inventory.append(record)
            return {**expected, **record}

        result_chunks = []
        for chunk in index["chunks"]:
            result = {"id": chunk["id"], "bounds_px": chunk["bounds_px"], "outputs": {}}
            for layer, record in chunk["outputs"].items():
                applicable = [
                    repair
                    for repair in repairs
                    if repair["manifest"]["layer"] == layer
                    and intersect(repair["manifest"]["bounds_px"], chunk["bounds_px"])
                ]
                source = safe_path(index_path.parent, record["path"])
                if applicable:
                    relative = f"layers/{layer}/{chunk['id']}.png"
                    destination = safe_path(staging, relative)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with Image.open(source) as image:
                        if image.mode not in {"RGB", "RGBA", "P", "L", "LA"}:
                            raise ValueError("Repairs require 8-bit source images")
                        canvas = image.convert("RGBA")
                    for repair in applicable:
                        manifest = repair["manifest"]
                        bounds = manifest["bounds_px"]
                        box = intersect(bounds, chunk["bounds_px"])
                        with Image.open(
                            repair["directory"] / manifest["files"]["repaired"]["path"]
                        ) as replacement:
                            patch = replacement.crop(
                                (
                                    box[0] - bounds[0],
                                    box[1] - bounds[1],
                                    box[2] - bounds[0],
                                    box[3] - bounds[1],
                                )
                            )
                        # Exact replacement, including alpha; never blend source pixels.
                        canvas.paste(
                            patch,
                            (
                                box[0] - chunk["bounds_px"][0],
                                box[1] - chunk["bounds_px"][1],
                            ),
                        )
                        patch.close()
                    new_record = clean_png(canvas, destination)
                    canvas.close()
                    new_record["path"] = relative
                    result["outputs"][layer] = new_record
                    inventory.append(
                        {key: new_record[key] for key in ("path", "sha256", "bytes")}
                    )
                else:
                    relative = f"layers/{layer}/{chunk['id']}{source.suffix}"
                    result["outputs"][layer] = copy_record(source, relative, record)
            if chunk.get("guides"):
                result["guides"] = {}
                for name, record in chunk["guides"].items():
                    source = safe_path(index_path.parent, record["path"])
                    relative = f"guides/{name}/{chunk['id']}{source.suffix}"
                    result["guides"][name] = copy_record(source, relative, record)
            result_chunks.append(result)
        result = {
            key: copy.deepcopy(index[key])
            for key in (
                "atlas_id",
                "release_id",
                "config",
                "geometry",
                "provenance",
                "tool_versions",
                "guide_metadata",
                "render_settings",
                "ai_styling",
            )
            if key in index
        }
        if result.get("guide_metadata"):
            feature = result["guide_metadata"]["feature_id"]
            source = safe_path(index_path.parent, feature["registry"])
            relative = "guides/feature-registry.json"
            copy_record(
                source,
                relative,
                {"sha256": feature["registry_sha256"], "bytes": source.stat().st_size},
            )
            feature["registry"] = relative
        repair_records = []
        for repair in repairs:
            manifest = repair["manifest"]
            for source in sorted(repair["directory"].iterdir()):
                copy_record(
                    source,
                    f"repairs/{manifest['id']}/{source.name}",
                    {"sha256": digest(source), "bytes": source.stat().st_size},
                )
            repair_records.append(
                {
                    "job_id": repair["job_id"],
                    "repair_id": manifest["id"],
                    "bounds_px": manifest["bounds_px"],
                    "layer": manifest["layer"],
                    "manifest_path": f"repairs/{manifest['id']}/manifest.json",
                    "manifest_sha256": repair["manifest_sha256"],
                }
            )
        result.update(
            {
                "schema_version": 1,
                "status": "complete",
                "kind": "repaired-chunk-release",
                "algorithm": "approved-repair-exact-splice-v1",
                "config_sha256": index["config_sha256"],
                "bounds_px": index["bounds_px"],
                "chunk_size_px": index["chunk_size_px"],
                "chunk_count": len(result_chunks),
                "layers": index["layers"],
                "chunks": result_chunks,
                "source_index_sha256": index_hash,
                "source_ai_styling": index["ai_styling"],
                "repairs": repair_records,
                "source_coverage_verified": False,
                "artwork_approved": False,
                "ai_styling": index["ai_styling"]
                or any(
                    repair["manifest"]["edit_method"] == "ai-assisted"
                    for repair in repairs
                ),
                "guides_modified": False,
                "release_review_required": True,
                "release_files": sorted(inventory, key=lambda record: record["path"]),
            }
        )
        if sum(record["bytes"] for record in inventory) > MAX_BYTES:
            raise ValueError("Encoded release exceeds bounded study byte limit")
        ident = content_id(result)
        result["repair_release_sha256"] = ident
        write_json(staging / "render-index.json", result)
        destination = output / ident
        verify_release(staging, expected_identity=ident)
        # Serialize final state validation and directory publication with ledger writes.
        with ledger.transaction():
            load_repairs(index_path, index, chunks, identifiers, ledger, workspace)
            if digest(index_path) != index_hash:
                raise ValueError("Source index changed while applying repairs")
            for record in records:
                checked_asset(index_path.parent, record)
            if destination.exists():
                if verify_release(destination) != result:
                    raise ValueError("Existing immutable repair release differs")
            else:
                staging.rename(destination)
        verify_release(destination)
        return {
            "id": ident,
            "directory": str(destination),
            "index": str(destination / "render-index.json"),
            "repairs": len(repairs),
            "artwork_approved": False,
        }
    finally:
        ledger.close()
        if staging is not None and staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path)
    parser.add_argument("--database", type=Path, default=Path(".cache/jobs.sqlite"))
    parser.add_argument("--jobs", nargs="+")
    parser.add_argument("--output", type=Path, default=Path(".cache/repaired-releases"))
    parser.add_argument("--workspace", type=Path, default=Path("."))
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        result = verify_release(args.verify)
        result = {
            "id": result["repair_release_sha256"],
            "chunks": result["chunk_count"],
            "repairs": len(result["repairs"]),
        }
    else:
        if not args.index or not args.jobs:
            parser.error("--index and explicit --jobs are required")
        result = apply_repairs(
            args.index, args.database, args.jobs, args.output, workspace=args.workspace
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

"""Publish bounded native AI maps with immutable parent and seam evidence.

Matching pixel frames are not proof of geographic registration. The exporter
checks exact native crops and ordered repair overwrites, never visual fidelity.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from pathlib import Path

from atlas import load_config
from build_preview_release import ATTRIBUTION, safe_file
from chunk_pyramid import build_pyramid, read_index, verify_pyramid
from PIL import Image, features
from PIL import __version__ as pillow_version

VERSION = 1
SIZE = 3072
CORE = 1024
MAX_BYTES = 512 * 1024 * 1024
MAX_IMAGE_PIXELS = 32_000_000
MAX_REPAIRS = 32
HASH = re.compile(r"[a-f0-9]{64}")
ID = re.compile(r"shinjuku-map-[a-f0-9]{24}")
EXPANSION_BOUNDS = (-1024, -1024, 4096, 4096)
LEGACY_BOUNDS = (0, 0, SIZE, SIZE)
ROOT = Path(__file__).resolve().parents[1]
JOINS = {f"v{x}-r{y}" for x in (1, 2) for y in range(3)} | {
    f"h{y}-c{x}" for y in (1, 2) for x in range(3)
}
JUNCTIONS = {f"x{x}-y{y}" for x in (1, 2) for y in (1, 2)}
PUBLIC_INPUTS = {
    "THIRD_PARTY_NOTICES.md": "NOTICE.txt",
    "LICENSE": "LICENSE",
    **{
        f"datasets/shinjuku-vegetation/{name}": f"data/{name}"
        for name in (
            "vegetation.json.gz",
            "NOTICE.txt",
            "README.txt",
            "query.overpassql",
            "SHA256SUMS",
        )
    },
}


def public_inputs(evidence):
    """Choose only the explicitly attributed database for this source edition."""
    if evidence["schema_version"] == 1:
        return PUBLIC_INPUTS
    return {
        name.replace(
            "datasets/shinjuku-vegetation/", "datasets/shinjuku-expansion-vegetation/"
        ): target
        for name, target in PUBLIC_INPUTS.items()
    }


def validate_source_provenance(value):
    if not isinstance(value, dict) or set(value) != {"building_sources", "vegetation"}:
        raise ValueError("Expansion source provenance is required")
    sources, vegetation = value["building_sources"], value["vegetation"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= 8:
        raise ValueError("Expected bounded actual building source editions")
    for item in sources:
        if not isinstance(item, dict) or set(item) != {"label", "url"}:
            raise ValueError("Building source attribution requires label and URL")
        text(item["label"], 500)
        url = text(item["url"], 500)
        if not re.fullmatch(
            r"https://www\.geospatial\.jp/ckan/dataset/[a-z0-9-]+", url
        ):
            raise ValueError("Expected an official PLATEAU source dataset URL")
    if len({item["url"] for item in sources}) != len(sources):
        raise ValueError("Duplicate building dataset attribution")
    if not isinstance(vegetation, dict) or set(vegetation) != {
        "snapshot_range",
        "public_data_sha256",
    }:
        raise ValueError(
            "Vegetation provenance requires snapshot and adapted database hash"
        )
    snapshot_range, checksum = (
        vegetation["snapshot_range"],
        vegetation["public_data_sha256"],
    )
    if (
        not isinstance(snapshot_range, list)
        or len(snapshot_range) != 2
        or any(
            not isinstance(timestamp, str)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp)
            for timestamp in snapshot_range
        )
        or snapshot_range != sorted(snapshot_range)
        or not isinstance(checksum, str)
        or not HASH.fullmatch(checksum)
    ):
        raise ValueError("Invalid vegetation snapshot or adapted database hash")
    return value


def verify_expansion_database(path, evidence):
    """Bind the public adapted database and displayed snapshot to the source receipt."""
    if evidence["schema_version"] == 1:
        return
    provenance = validate_source_provenance(evidence["source_provenance"])["vegetation"]
    if digest(path) != provenance["public_data_sha256"]:
        raise ValueError(
            "Expanded vegetation database differs from rendered source provenance"
        )
    with gzip.open(path, "rb") as stream:
        raw = stream.read(64_000_001)
    if len(raw) > 64_000_000:
        raise ValueError("Expanded vegetation database exceeds bounded decode size")
    database = json.loads(raw, object_pairs_hook=unique_object)
    if database.get("source", {}).get("snapshot_range") != provenance["snapshot_range"]:
        raise ValueError(
            "Displayed source snapshot differs from the actual vegetation database"
        )


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            result.update(block)
    return result.hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def read_json(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 20_000_000:
        raise ValueError("Expected bounded regular JSON file")
    return json.loads(path.read_text(), object_pairs_hook=unique_object)


def text(value, limit=8000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("Expected bounded nonempty provenance text")
    if any(
        marker in value
        for marker in ("/Users/", "/home/", "/private/", "file://", "\\Users\\")
    ):
        raise ValueError("Local filesystem paths are not public provenance")
    return value


def inventory(directory):
    records, total = [], 0
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Expected a regular release directory")
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("Symlinks and special release files are forbidden")
        if path.is_file():
            total += path.stat().st_size
            if total > MAX_BYTES or len(records) >= 100_000:
                raise ValueError("Map release exceeds bounded size")
            records.append(
                {
                    "path": path.relative_to(directory).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": digest(path),
                }
            )
    return records


def image(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 64_000_000:
        raise ValueError("Expected bounded regular image")
    with Image.open(path) as source:
        if (
            source.format != "PNG"
            or source.mode != "RGB"
            or source.info
            or source.width * source.height > MAX_IMAGE_PIXELS
        ):
            raise ValueError("Expected bounded metadata-free RGB PNG")
        source.load()
        return source.copy()


def rectangle(value):
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(type(n) is not int for n in value)
        or value[0] >= value[2]
        or value[1] >= value[3]
    ):
        raise ValueError("Expected nonempty integer pixel rectangle")
    if any(abs(n) > 100_000 for n in value):
        raise ValueError("Pixel rectangle exceeds study bounds")
    return value


def validate_config(config_path, index):
    config = load_config(config_path)
    if (
        config.bounds_px not in (LEGACY_BOUNDS, EXPANSION_BOUNDS)
        or config.chunk_size_px != CORE
        or config.pixel_origin != (SIZE / 2, SIZE / 2)
        or not math.isclose(
            config.pixels_per_metre, SIZE / 1260, rel_tol=0, abs_tol=1e-12
        )
        or config.origin_lonlat != (139.6917, 35.6896)
        or config.camera_offset_enu != (1500.0, -1500.0, 1500.0)
        or config.camera_target_enu != (0.0, 0.0, 120.08777160644532)
    ):
        raise ValueError(
            "Expected the 3072-pixel study with 1024-pixel cores and original world framing"
        )
    if index.get("config_sha256") != config.fingerprint:
        raise ValueError("Source configuration identity mismatch")
    return config


def review_regions(bounds=LEGACY_BOUNDS):
    """Named global review regions, returned as canvas-local rectangles."""
    x0, y0, x1, y1 = (n // CORE for n in bounds)
    regions = {}
    for x in range(x0 + 1, x1):
        for y in range(y0, y1):
            regions[f"v{x}-r{y}"] = [
                x * CORE - 192,
                y * CORE,
                x * CORE + 192,
                (y + 1) * CORE,
            ]
    for y in range(y0 + 1, y1):
        for x in range(x0, x1):
            regions[f"h{y}-c{x}"] = [
                x * CORE,
                y * CORE - 192,
                (x + 1) * CORE,
                y * CORE + 192,
            ]
    for x in range(x0 + 1, x1):
        for y in range(y0 + 1, y1):
            regions[f"x{x}-y{y}"] = [
                x * CORE - 256,
                y * CORE - 256,
                x * CORE + 256,
                y * CORE + 256,
            ]
    return {
        name: [
            box[0] - bounds[0],
            box[1] - bounds[1],
            box[2] - bounds[0],
            box[3] - bounds[1],
        ]
        for name, box in regions.items()
    }


def validate_review(review, bounds=LEGACY_BOUNDS):
    regions = review_regions(bounds)
    joins = {name for name in regions if name.startswith(("v", "h"))}
    junctions = set(regions) - joins
    if (
        not isinstance(review, dict)
        or set(review) != {"status", "joins", "junctions", "note", "binding"}
        or review["status"] != "reviewed"
    ):
        raise ValueError("Explicit completed visual seam review is required")
    text(review["note"])
    binding = review["binding"]
    if (
        not isinstance(binding, dict)
        or set(binding)
        != {"atlas_rgb_sha256", "source_rgb_sha256", "regions_rgb_sha256"}
        or not isinstance(binding["regions_rgb_sha256"], dict)
        or set(binding["regions_rgb_sha256"]) != joins | junctions
        or any(
            not isinstance(value, str) or not HASH.fullmatch(value)
            for value in (
                binding["atlas_rgb_sha256"],
                binding["source_rgb_sha256"],
                *binding["regions_rgb_sha256"].values(),
            )
        )
    ):
        raise ValueError("Visual review requires complete RGB image bindings")
    for field, expected in (("joins", joins), ("junctions", junctions)):
        records = review[field]
        if not isinstance(records, list) or len(records) != len(expected):
            raise ValueError("Visual review must cover every join and junction")
        if any(
            not isinstance(record, dict)
            or set(record) != {"id", "passed", "note"}
            or record["passed"] is not True
            for record in records
        ):
            raise ValueError("Every seam review item must explicitly pass")
        if {record["id"] for record in records} != expected:
            raise ValueError("Seam review IDs are missing or repeated")
        for record in records:
            text(record["note"])


def validate_source_comparison(review):
    if (
        not isinstance(review, dict)
        or set(review) != {"status", "passed", "note"}
        or review["status"] != "reviewed"
        or review["passed"] is not True
    ):
        raise ValueError("Explicit passing source comparison review is required")
    text(review["note"])


def rgb_digest(picture):
    """Hash bounded row strips to avoid another full decoded-frame allocation."""
    result = hashlib.sha256()
    for top in range(0, picture.height, 128):
        with picture.crop(
            (0, top, picture.width, min(top + 128, picture.height))
        ) as strip:
            result.update(strip.tobytes())
    return result.hexdigest()


def review_binding(atlas, source, bounds=LEGACY_BOUNDS):
    """Bind decisions to exact native RGB pixels and global-coordinate regions."""
    size = (bounds[2] - bounds[0], bounds[3] - bounds[1])
    if any(
        picture.mode != "RGB" or picture.size != size for picture in (atlas, source)
    ):
        raise ValueError(
            f"Review binding requires two native {size[0]}-square RGB frames"
        )
    hashes = {}
    for identifier, box in review_regions(bounds).items():
        with atlas.crop(box) as region:
            hashes[identifier] = rgb_digest(region)
    return {
        "atlas_rgb_sha256": rgb_digest(atlas),
        "source_rgb_sha256": rgb_digest(source),
        "regions_rgb_sha256": hashes,
    }


def generation(record, base, bounds):
    """Check a direct native crop, without changing its scale or alpha."""
    output = record["generation_output"]
    if (
        not isinstance(output, dict)
        or set(output) != {"path", "sha256"}
        or not isinstance(output["sha256"], str)
        or not HASH.fullmatch(output["sha256"])
    ):
        raise ValueError("Generation output requires its relative path and SHA256")
    path = safe_file(base, output["path"])
    if not path.is_file() or path.stat().st_size > 64_000_000:
        raise ValueError("Expected bounded generation output")
    if digest(path) != output["sha256"]:
        raise ValueError("Generation output checksum mismatch")
    crop, window = (
        rectangle(record["crop_box_px"]),
        rectangle(record["source_bounds_px"]),
    )
    if [
        window[0] + crop[0],
        window[1] + crop[1],
        window[0] + crop[2],
        window[1] + crop[3],
    ] != bounds:
        raise ValueError(
            "Native crop does not occupy its declared global source bounds"
        )
    references = record["reference_sha256"]
    if (
        not isinstance(references, list)
        or not 1 <= len(references) <= 16
        or any(
            not isinstance(value, str) or not HASH.fullmatch(value)
            for value in references
        )
    ):
        raise ValueError("Generation reference checksums are required")
    with image(path) as source:
        if (
            source.size != (window[2] - window[0], window[3] - window[1])
            or crop[0] < 0
            or crop[1] < 0
            or crop[2] > source.width
            or crop[3] > source.height
        ):
            raise ValueError("Generation dimensions and native crop disagree")
        return path, source.crop(crop)


def parent_proof(directory, ident):
    """Verify a legacy release's retained native proof against its immutable ID.

    Browsing pyramid assets need not be copied. Their original checksums remain
    in the content-addressed identity, while every native proof file is retained.
    Expansion parents are deliberately limited to one verified legacy pilot.
    """
    directory = Path(directory)
    release = read_json(directory / "release.json")
    if (
        set(release) != {"schema_version", "id", "identity"}
        or release["schema_version"] != 1
        or release["id"] != ident
        or not isinstance(ident, str)
        or not ID.fullmatch(ident)
        or release_id(release["identity"]) != ident
    ):
        raise ValueError("Parent immutable release identity mismatch")
    identity = release["identity"]
    if (
        set(identity)
        != {"exporter_version", "code", "tools", "input_index_sha256", "assets"}
        or identity["exporter_version"] != 1
    ):
        raise ValueError("Invalid parent release identity schema")
    evidence = read_json(directory / "evidence.json")
    if evidence.get("schema_version") != 1:
        raise ValueError("Expansion requires a legacy nine-patch parent")
    _, index, evidence, files = validate_inputs(
        directory / "source-index.json",
        directory / "source-config.json",
        directory / "evidence.json",
    )
    files.update(
        {
            name: directory / name
            for name in (
                "source-index.json",
                "source-config.json",
                "evidence.json",
                *PUBLIC_INPUTS.values(),
            )
        }
    )
    inventory_records = release["identity"].get("assets")
    if not isinstance(inventory_records, list):
        raise ValueError("Parent release inventory is missing")
    inventory_by_name = {record["path"]: record for record in inventory_records}
    if len(inventory_by_name) != len(inventory_records):
        raise ValueError("Parent release inventory has duplicate paths")
    for name, path in files.items():
        record = inventory_by_name.get(name)
        if record != {
            "path": name,
            "bytes": path.stat().st_size,
            "sha256": digest(path),
        }:
            raise ValueError("Parent native proof differs from its immutable release")
    files["release.json"] = directory / "release.json"
    return index, evidence, files


def validate_expansion_parent(evidence, base):
    parent = evidence["parent"]
    if (
        not isinstance(parent, dict)
        or set(parent) != {"id", "release_path"}
        or parent["release_path"] != "parent/release.json"
    ):
        raise ValueError("Expansion requires its bounded immutable parent proof")
    index, original, files = parent_proof(safe_file(base, "parent"), parent["id"])
    inherited = {record["id"]: record for record in original["patches"]}
    patches = {record["id"]: record for record in evidence["patches"]}
    for ident, previous in inherited.items():
        actual = patches.get(ident)
        expected = json.loads(json.dumps(previous))
        expected["generation_output"]["path"] = (
            "parent/" + expected["generation_output"]["path"]
        )
        if actual is None or {
            key: value
            for key, value in actual.items()
            if key not in {"styled_sha256", "source_sha256"}
        } != {
            key: value
            for key, value in expected.items()
            if key not in {"styled_sha256", "source_sha256"}
        }:
            raise ValueError("Inherited generation provenance changed")
    historical = json.loads(json.dumps(original["repairs"]))
    for record in historical:
        record["generation_output"]["path"] = (
            "parent/" + record["generation_output"]["path"]
        )
    repairs = evidence["repairs"]
    if repairs[: len(historical)] != historical or len(repairs) > len(historical) + 8:
        raise ValueError(
            "Expansion must preserve ordered parent repairs and at most eight new repairs"
        )
    for record in repairs[len(historical) :]:
        window = rectangle(record["source_bounds_px"])
        box = rectangle(record["bounds_px"])
        if (
            window[2] - window[0] != 1254
            or window[3] - window[1] != 1254
            or window[0] < -1024
            or window[1] < -1024
            or window[2] > 4096
            or window[3] > 4096
            or (box[2] - box[0]) * (box[3] - box[1]) > CORE * CORE
        ):
            raise ValueError(
                "Expansion repairs require bounded native 1254 generation windows"
            )
    for record in evidence["patches"]:
        if record["id"] not in inherited:
            window = rectangle(record["source_bounds_px"])
            if (
                window[2] - window[0] != 1254
                or window[3] - window[1] != 1254
                or window[0] < -1024
                or window[1] < -1024
                or window[2] > 4096
                or window[3] > 4096
            ):
                raise ValueError(
                    "Expansion generation must use a native bounded 1254 window"
                )
    return {"parent/" + name: path for name, path in files.items()}


def validate_inputs(index_path, config_path, evidence_path):
    read_json(index_path)  # Bound size and reject duplicate fields before parsing.
    index, chunks, _ = read_index(index_path)
    config = validate_config(config_path, index)
    bounds = config.bounds_px
    expanded = bounds == EXPANSION_BOUNDS
    count = 25 if expanded else 9
    dimensions = (bounds[2] - bounds[0], bounds[3] - bounds[1])
    if (
        index.get("ai_styling") is not True
        or index["bounds_px"] != list(bounds)
        or index["chunk_size_px"] != CORE
        or index["layers"] != ["styled", "textured"]
        or len(chunks) != count
    ):
        raise ValueError(
            f"Expected {count} AI-assisted styled/source chunks on the fixed study grid"
        )
    if expanded:
        expected_cells = {
            f"x{x}_y{y}": [x * CORE, y * CORE, (x + 1) * CORE, (y + 1) * CORE]
            for x in range(-1, 4)
            for y in range(-1, 4)
        }
        if {chunk["id"]: chunk["bounds_px"] for chunk in chunks} != expected_cells:
            raise ValueError("Expansion requires exact signed global cell identities")
    evidence = read_json(evidence_path)
    fields = {
        "schema_version",
        "artwork_method",
        "native_artwork",
        "upscaled_artwork",
        "artwork_approved",
        "geographic_fidelity_approved",
        "comparison_mode",
        "comparison_note",
        "generation_tool",
        "model_version",
        "prompt",
        "patches",
        "repairs",
        "seam_review",
        "source_comparison",
    }
    if expanded:
        fields.update({"parent", "source_provenance"})
    if (
        not isinstance(evidence, dict)
        or set(evidence) != fields
        or type(evidence["schema_version"]) is not int
        or evidence["schema_version"] != (2 if expanded else VERSION)
    ):
        raise ValueError("Invalid map evidence schema")
    if (
        evidence["artwork_method"] != "ai-assisted"
        or evidence["native_artwork"] is not True
        or evidence["upscaled_artwork"] is not False
        or evidence["artwork_approved"] is not False
        or evidence["geographic_fidelity_approved"] is not False
    ):
        raise ValueError("Native artwork declarations cannot imply geographic approval")
    if evidence["comparison_mode"] not in {
        "shared-framing-unverified",
        "reviewed-approximate",
    }:
        raise ValueError("AI imagery cannot claim exact geographic registration")
    for field in ("comparison_note", "generation_tool", "model_version", "prompt"):
        text(evidence[field], 700 if field == "comparison_note" else 8000)
    validate_review(evidence["seam_review"], bounds)
    validate_source_comparison(evidence["source_comparison"])
    patches, repairs = evidence["patches"], evidence["repairs"]
    if (
        not isinstance(patches, list)
        or len(patches) != count
        or not isinstance(repairs, list)
        or len(repairs) > MAX_REPAIRS
    ):
        raise ValueError(f"Expected {count} main patches and at most 32 repairs")
    chunk_by_id = {chunk["id"]: chunk for chunk in chunks}
    if any(not isinstance(record, dict) for record in patches) or {
        record.get("id") for record in patches
    } != set(chunk_by_id):
        raise ValueError("Generation patches must match all source chunks exactly")
    parent_sources = {}
    if expanded:
        validate_source_provenance(evidence["source_provenance"])
        parent_sources = validate_expansion_parent(evidence, evidence_path.parent)
    normalized = json.loads(json.dumps(evidence))
    outputs = {}
    sources = dict(parent_sources)
    hashes = set()
    try:
        for record, public in zip(patches, normalized["patches"], strict=True):
            expected_fields = {
                "id",
                "styled_sha256",
                "source_sha256",
                "generation_output",
                "crop_box_px",
                "source_bounds_px",
                "reference_sha256",
            }
            if set(record) - {"prompt"} != expected_fields:
                raise ValueError("Unexpected patch provenance fields")
            if "prompt" in record:
                text(record["prompt"])
            chunk = chunk_by_id[record["id"]]
            if (
                record["styled_sha256"] != chunk["outputs"]["styled"]["sha256"]
                or record["source_sha256"] != chunk["outputs"]["textured"]["sha256"]
            ):
                raise ValueError("Patch evidence disagrees with finalized chunk hashes")
            path, pixels = generation(record, evidence_path.parent, chunk["bounds_px"])
            outputs[record["id"]] = pixels
            if record["generation_output"]["sha256"] in hashes:
                raise ValueError(
                    "All patches require distinct native generation outputs"
                )
            hashes.add(record["generation_output"]["sha256"])
            inherited = expanded and record["generation_output"]["path"].startswith(
                "parent/"
            )
            name = (
                record["generation_output"]["path"]
                if inherited
                else f"generation/{record['id']}.png"
            )
            sources[name] = path
            public["generation_output"]["path"] = name
        repair_ids = set()
        for position, (record, public) in enumerate(
            zip(repairs, normalized["repairs"], strict=True)
        ):
            if (
                not isinstance(record, dict)
                or set(record) - {"prompt"}
                != {
                    "id",
                    "bounds_px",
                    "generation_output",
                    "crop_box_px",
                    "source_bounds_px",
                    "reference_sha256",
                }
                or not isinstance(record["id"], str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", record["id"])
                or record["id"] in repair_ids
            ):
                raise ValueError("Invalid or duplicate repair provenance")
            if "prompt" in record:
                text(record["prompt"])
            repair_ids.add(record["id"])
            repair_bounds = rectangle(record["bounds_px"])
            if (
                repair_bounds[0] < bounds[0]
                or repair_bounds[1] < bounds[1]
                or repair_bounds[2] > bounds[2]
                or repair_bounds[3] > bounds[3]
                or (repair_bounds[2] - repair_bounds[0])
                * (repair_bounds[3] - repair_bounds[1])
                > 4_000_000
            ):
                raise ValueError("Repair rectangle is outside the bounded atlas")
            path, pixels = generation(record, evidence_path.parent, repair_bounds)
            try:
                for chunk in chunks:
                    core = chunk["bounds_px"]
                    intersection = [
                        max(core[0], repair_bounds[0]),
                        max(core[1], repair_bounds[1]),
                        min(core[2], repair_bounds[2]),
                        min(core[3], repair_bounds[3]),
                    ]
                    if (
                        intersection[0] < intersection[2]
                        and intersection[1] < intersection[3]
                    ):
                        crop = pixels.crop(
                            (
                                intersection[0] - repair_bounds[0],
                                intersection[1] - repair_bounds[1],
                                intersection[2] - repair_bounds[0],
                                intersection[3] - repair_bounds[1],
                            )
                        )
                        outputs[chunk["id"]].paste(
                            crop, (intersection[0] - core[0], intersection[1] - core[1])
                        )
                        crop.close()
            finally:
                pixels.close()
            inherited = expanded and record["generation_output"]["path"].startswith(
                "parent/"
            )
            name = (
                record["generation_output"]["path"]
                if inherited
                else f"generation/repair-{position:02}-{record['id']}.png"
            )
            sources[name] = path
            public["generation_output"]["path"] = name
        with (
            Image.new("RGB", dimensions) as atlas,
            Image.new("RGB", dimensions) as source,
        ):
            for chunk in chunks:
                for layer in index["layers"]:
                    path = chunk["outputs"][layer]["path"]
                    with image(path) as pixels:
                        if (
                            layer == "styled"
                            and pixels.tobytes() != outputs[chunk["id"]].tobytes()
                        ):
                            raise ValueError(
                                "Final artwork differs from native crops and ordered exact repairs"
                            )
                        target = atlas if layer == "styled" else source
                        target.paste(
                            pixels,
                            (
                                chunk["bounds_px"][0] - bounds[0],
                                chunk["bounds_px"][1] - bounds[1],
                            ),
                        )
                    sources[f"chunks/{chunk['id']}/{layer}.png"] = path
            if evidence["seam_review"]["binding"] != review_binding(
                atlas, source, bounds
            ):
                raise ValueError("Visual review RGB bindings do not match final pixels")
    finally:
        for pixels in outputs.values():
            pixels.close()
    clean_index = {
        key: index[key]
        for key in (
            "schema_version",
            "status",
            "ai_styling",
            "bounds_px",
            "chunk_size_px",
            "config_sha256",
            "layers",
        )
    }
    clean_index["chunks"] = [
        {
            "id": chunk["id"],
            "bounds_px": chunk["bounds_px"],
            "outputs": {
                layer: {
                    "path": f"chunks/{chunk['id']}/{layer}.png",
                    "width": CORE,
                    "height": CORE,
                    "bytes": chunk["outputs"][layer]["path"].stat().st_size,
                    "sha256": chunk["outputs"][layer]["sha256"],
                }
                for layer in index["layers"]
            },
        }
        for chunk in chunks
    ]
    return config, clean_index, normalized, sources


def metadata(config, evidence, notice):
    expanded = evidence["schema_version"] == 2
    provenance = (
        validate_source_provenance(evidence["source_provenance"]) if expanded else None
    )
    credits = (
        (
            provenance["building_sources"]
            + ATTRIBUTION[3:-1]
            + [
                {
                    "label": "Adapted expansion vegetation database and terms",
                    "url": "https://github.com/stianfro/isometric-tokyo/tree/main/datasets/shinjuku-expansion-vegetation",
                }
            ]
        )
        if expanded
        else ATTRIBUTION
    )
    edition = (
        (
            "PLATEAU FY2025 building sources listed in attribution; PLATEAU-Ortho 2023; OSM snapshots "
            + " to ".join(provenance["vegetation"]["snapshot_range"])
        )
        if expanded
        else "Shinjuku, Shibuya and Nakano FY2025; PLATEAU-Ortho 2023; OSM 2026-10-21"
    )
    return {
        "title": "Shinjuku, Pixel city native study",
        "place": {"name": "Shinjuku", "name_ja": "新宿"},
        "source_edition": edition,
        "ai_styling": True,
        "attribution": credits,
        "coverage": {
            "label": (
                "Shinjuku 25-patch expansion. Geographic bounds describe source acquisition, not surveyed artwork accuracy or complete district coverage."
                if evidence["schema_version"] == 2
                else "Western Shinjuku nine-patch study. Bounds describe the original central selection, not the full rendered view or completed district coverage."
            ),
            "bbox": list(config.source_bounds_wsen),
        },
        "modifications": "AI-assisted native patch generation, direct integer crops and recorded exact repair overwrites. Source textures retain their separate unstyled pixels. Independent project, no endorsement.",
        "limitations": "Shared source framing permits a limited comparison, not pixel-level geographic registration. AI may displace buildings, roads and vegetation. "
        + evidence["comparison_note"],
        "layer_labels": {"styled": "Pixel city", "textured": "Source render"},
        "notice_text": notice,
    }


def release_id(identity):
    return "shinjuku-map-" + hashlib.sha256(canonical(identity)).hexdigest()[:24]


def public_manifest(directory, ident):
    manifest = read_json(directory / "pyramid/manifest.json")
    manifest["id"] = ident
    for layer in manifest["layers"]:
        layer["dzi"] = f"releases/{ident}/pyramid/{layer['dzi']}"
    manifest["notice"] = f"releases/{ident}/NOTICE.txt"
    evidence = read_json(directory / "evidence.json")
    manifest.update(
        {
            "production_release": False,
            "comparison_mode": evidence["comparison_mode"],
            "geographic_fidelity_approved": False,
            "seam_review": "recorded-visual-review",
            "provenance": f"releases/{ident}/evidence.json",
        }
    )
    if evidence["schema_version"] == 2:
        manifest.update(
            {
                "coordinate_frame": {"id": "shinjuku-native-v1", "units": "pixels"},
                "legacy_view_bounds": [0, 0, SIZE, SIZE],
                "home_bounds": [0, 0, SIZE, SIZE],
            }
        )
    return manifest


def verify_pyramid_sources(pyramid, index):
    settings = pyramid["settings"]
    if any(
        settings[field] != index[field]
        for field in ("config_sha256", "bounds_px", "layers")
    ):
        raise ValueError("Pyramid settings do not match the finalized native sources")
    expected = sorted(
        [
            {
                "id": chunk["id"],
                "bounds_px": chunk["bounds_px"],
                "hashes": {
                    layer: chunk["outputs"][layer]["sha256"]
                    for layer in index["layers"]
                },
            }
            for chunk in index["chunks"]
        ],
        key=lambda chunk: chunk["id"],
    )
    if pyramid["sources"] != expected:
        raise ValueError(
            "Pyramid source hashes do not match the finalized native chunks"
        )


def verify_version(directory, ident):
    release = read_json(directory / "release.json")
    if (
        set(release) != {"schema_version", "id", "identity"}
        or type(release["schema_version"]) is not int
        or release["schema_version"] != VERSION
        or release["id"] != ident
        or not ID.fullmatch(ident)
    ):
        raise ValueError("Invalid map release record")
    identity = release["identity"]
    if (
        set(identity)
        != {"exporter_version", "code", "tools", "input_index_sha256", "assets"}
        or identity["exporter_version"] != VERSION
        or release_id(identity) != ident
    ):
        raise ValueError("Map release identity mismatch")
    actual = [
        record for record in inventory(directory) if record["path"] != "release.json"
    ]
    if actual != identity["assets"]:
        raise ValueError("Map release asset checksum or inventory mismatch")
    config, index, evidence, sources = validate_inputs(
        directory / "source-index.json",
        directory / "source-config.json",
        directory / "evidence.json",
    )
    public_paths = (
        set(PUBLIC_INPUTS.values())
        | {"source-index.json", "source-config.json", "evidence.json"}
        | set(sources)
    )
    if not public_paths.issubset({record["path"] for record in actual}):
        raise ValueError("Required public provenance or licence files are missing")
    for record in actual:
        if record["path"] not in public_paths and not record["path"].startswith(
            "pyramid/"
        ):
            raise ValueError("Unexpected map release asset")
    data_paths = set()
    for line in (directory / "data/SHA256SUMS").read_text().splitlines():
        checksum, name = line.split(maxsplit=1)
        name = name.lstrip("*")
        if (
            name in data_paths
            or digest(safe_file(directory / "data", name)) != checksum
        ):
            raise ValueError("Vegetation data checksum mismatch")
        data_paths.add(name)
    if data_paths != {
        "vegetation.json.gz",
        "NOTICE.txt",
        "README.txt",
        "query.overpassql",
    }:
        raise ValueError("Vegetation checksum inventory is incomplete")
    verify_expansion_database(directory / "data/vegetation.json.gz", evidence)
    pyramid = verify_pyramid(directory / "pyramid")
    pyramid_index = read_json(directory / "pyramid/pyramid-index.json")
    verify_pyramid_sources(pyramid_index, index)
    settings = pyramid_index["settings"]
    if settings["viewer_metadata"] != metadata(
        config, evidence, (directory / "NOTICE.txt").read_text()
    ):
        raise ValueError("Pyramid provenance differs from release evidence")
    manifest = public_manifest(directory, ident)
    if (
        manifest["width"] != config.bounds_px[2] - config.bounds_px[0]
        or manifest["height"] != config.bounds_px[3] - config.bounds_px[1]
        or manifest["artwork_approved"] is not False
        or manifest["source_coverage_verified"] is not False
    ):
        raise ValueError("Map release cannot imply geographic or artwork approval")
    return {
        "id": ident,
        "tiles": pyramid["tiles"],
        "assets": len(actual) + 1,
        "bytes": sum(record["bytes"] for record in actual)
        + (directory / "release.json").stat().st_size,
    }


def verify_release(output):
    manifest = read_json(safe_file(output, "manifest.json"))
    ident = manifest.get("id")
    if not isinstance(ident, str) or not ID.fullmatch(ident):
        raise ValueError("Expected an immutable native map release")
    directory = safe_file(output, f"releases/{ident}")
    result = verify_version(directory, ident)
    if manifest != public_manifest(directory, ident):
        raise ValueError("Active manifest disagrees with immutable map release")
    return result


def build_release(index_path, config_path, evidence_path, output, repo_root=ROOT):
    index_path, config_path, evidence_path, output, repo_root = map(
        Path, (index_path, config_path, evidence_path, output, repo_root)
    )
    config, clean_index, evidence, sources = validate_inputs(
        index_path, config_path, evidence_path
    )
    sources.update(
        {
            destination: safe_file(repo_root, source)
            for source, destination in public_inputs(evidence).items()
        }
    )
    verify_expansion_database(sources["data/vegetation.json.gz"], evidence)
    initial = {
        str(path): digest(path)
        for path in [index_path, config_path, evidence_path, *sources.values()]
    }
    if sum(path.stat().st_size for path in sources.values()) > MAX_BYTES:
        raise ValueError("Map release sources exceed bounded size")
    if output.is_symlink() or (
        output.exists()
        and (not output.is_dir() or not (output / ".preview-build").is_file())
    ):
        raise ValueError("Refusing an unowned map output directory")
    if any(
        path.resolve().is_relative_to(output.resolve())
        for path in (index_path, config_path, evidence_path, *sources.values())
    ):
        raise ValueError("Map output cannot contain its own source inputs")
    output.mkdir(parents=True, exist_ok=True)
    safe_file(output, ".preview-build").write_text(
        "isometric.tokyo bounded public releases\n"
    )
    safe_file(output, "releases").mkdir(exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".map-stage-", dir=output))
    try:
        for name, path in sources.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        (staging / "source-config.json").write_bytes(config_path.read_bytes())
        (staging / "source-index.json").write_bytes(canonical(clean_index))
        (staging / "evidence.json").write_bytes(canonical(evidence))
        build_pyramid(
            staging / "source-index.json",
            staging / "pyramid",
            tile_size=256,
            overlap=1,
            incremental=False,
            viewer_metadata=metadata(
                config, evidence, (staging / "NOTICE.txt").read_text()
            ),
        )
        identity = {
            "exporter_version": VERSION,
            "code": {
                name: digest(Path(__file__).with_name(name))
                for name in (
                    "build_map_release.py",
                    "chunk_pyramid.py",
                    "viewer_manifest.py",
                )
            },
            "tools": {
                "pillow": pillow_version,
                "webp": features.version_module("webp"),
            },
            "input_index_sha256": initial[str(index_path)],
            "assets": inventory(staging),
        }
        ident = release_id(identity)
        (staging / "release.json").write_bytes(
            canonical({"schema_version": VERSION, "id": ident, "identity": identity})
        )
        result = verify_version(staging, ident)
        if initial != {
            str(path): digest(path)
            for path in [index_path, config_path, evidence_path, *sources.values()]
        }:
            raise ValueError("Inputs changed during map release construction")
        destination = safe_file(output, f"releases/{ident}")
        if destination.exists():
            verify_version(destination, ident)
        else:
            staging.rename(destination)
        manifest = public_manifest(destination, ident)
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".map-manifest-", dir=output, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(canonical(manifest))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.replace(temporary, output / "manifest.json")
        finally:
            temporary.unlink(missing_ok=True)
        return result
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--output", type=Path, default=Path("viewer/public/preview"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        result = verify_release(args.output)
    else:
        if any(value is None for value in (args.index, args.config, args.evidence)):
            parser.error("--index, --config and --evidence are required to build")
        result = build_release(
            args.index, args.config, args.evidence, args.output, args.root
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

"""Validate exact source guide artifacts and their cross-chunk equivalence."""

from __future__ import annotations

import json
import math
import sys
from array import array
from pathlib import Path

from PIL import Image

PNG_GUIDES = {"feature-id", "semantic", "silhouette", "normal-preview", "depth-preview"}
BINARY_GUIDES = {"depth": 1, "normal": 3}


def _floats(payload):
    result = array("f")
    result.frombytes(payload)
    if sys.byteorder != "little":
        result.byteswap()
    if any(not math.isfinite(value) for value in result):
        raise ValueError("Numeric guide contains NaN or infinity")
    return result


def verify_guides(index: dict, root: Path, *, compare_reference=False):
    from render_geometry import file_hash, relative_asset

    metadata = index.get("guide_metadata")
    if not metadata:
        raise ValueError("Render has no numeric or categorical guides")
    registry_path = relative_asset(root, metadata["feature_id"]["registry"])
    if file_hash(registry_path) != metadata["feature_id"]["registry_sha256"]:
        raise ValueError("Feature registry checksum mismatch")
    registry = json.loads(registry_path.read_text())
    identifiers = {item["id"] for item in registry["records"]}
    if len(identifiers) != len(registry["records"]) or not {0, 1}.issubset(identifiers):
        raise ValueError(
            "Feature registry IDs must be unique and include background and ground"
        )
    frames = index["chunks"] + ([index["reference"]] if index.get("reference") else [])
    building_pixels = 0
    for frame in frames:
        guides = frame.get("guides", {})
        if set(guides) != PNG_GUIDES | set(BINARY_GUIDES):
            raise ValueError("Every frame must contain all seven guide artifacts")
        left, top, right, bottom = frame["bounds_px"]
        size = right - left, bottom - top
        pixels = size[0] * size[1]
        loaded = {}
        for name, asset in guides.items():
            path = relative_asset(root, asset["path"])
            if file_hash(path) != asset["sha256"]:
                raise ValueError(f"Guide checksum mismatch: {name}")
            if (asset["width"], asset["height"]) != size:
                raise ValueError(
                    "Guide manifest dimensions do not match its pixel rectangle"
                )
            if name in BINARY_GUIDES:
                payload = path.read_bytes()
                if len(payload) != pixels * BINARY_GUIDES[name] * 4:
                    raise ValueError(
                        "Numeric guide byte count does not match its shape"
                    )
                loaded[name] = _floats(payload)
            else:
                with Image.open(path) as image:
                    image.load()
                    if image.size != size:
                        raise ValueError(
                            "Guide PNG dimensions do not match its pixel rectangle"
                        )
                    modes = (
                        {"RGB"}
                        if name in ("feature-id", "normal-preview")
                        else {"I", "I;16", "I;16B"}
                        if name == "depth-preview"
                        else {"L"}
                    )
                    if image.mode not in modes:
                        raise ValueError("Guide PNG encoding changed")
                    loaded[name] = list(image.getdata())
        for pixel, semantic in enumerate(loaded["semantic"]):
            red, green, blue = loaded["feature-id"][pixel]
            feature = red * 65536 + green * 256 + blue
            if feature not in identifiers or semantic not in (0, 1, 2):
                raise ValueError("Guide contains an unknown feature or semantic ID")
            if (semantic == 0) != (feature == 0) or (semantic == 1) != (feature == 1):
                raise ValueError(
                    "Guide feature ID disagrees with its semantic category"
                )
            if loaded["silhouette"][pixel] != (255 if semantic == 2 else 0):
                raise ValueError("Building silhouette disagrees with semantic guide")
            normal = loaded["normal"][pixel * 3 : pixel * 3 + 3]
            if semantic == 0:
                if (
                    loaded["depth"][pixel] != 0
                    or any(normal)
                    or loaded["depth-preview"][pixel] != 0
                ):
                    raise ValueError("Background numeric guide pixels must be zero")
            elif abs(sum(value * value for value in normal) - 1) > 0.001:
                raise ValueError("Geometry normal is not a unit vector")
            if semantic == 2:
                building_pixels += 1
    result = {
        "schema_version": 1,
        "frame_count": len(frames),
        "source_building_registry_count": metadata["source_building_registry_count"],
        "building_pixel_samples": building_pixels,
        "source_coverage_verified": False,
        "artifacts_valid": True,
    }
    if not building_pixels:
        raise ValueError("No building pixels were sampled in guide artifacts")
    if compare_reference:
        if "reference" not in index:
            raise ValueError("Guide seam comparison requires a full-view reference")
        reference = index["reference"]
        ref_left, ref_top, ref_right, ref_bottom = reference["bounds_px"]
        if reference["bounds_px"] != index["bounds_px"]:
            raise ValueError("Guide reference extent differs from atlas extent")
        for name in PNG_GUIDES | set(BINARY_GUIDES):
            reference_path = relative_asset(root, reference["guides"][name]["path"])
            if name in BINARY_GUIDES:
                reference_bytes = reference_path.read_bytes()
                stride = (ref_right - ref_left) * BINARY_GUIDES[name] * 4
            else:
                with Image.open(reference_path) as image:
                    image.load()
                    reference_image = image.copy()
            for frame in index["chunks"]:
                left, top, right, bottom = frame["bounds_px"]
                chunk_path = relative_asset(root, frame["guides"][name]["path"])
                if name in BINARY_GUIDES:
                    offset = (left - ref_left) * BINARY_GUIDES[name] * 4
                    width_bytes = (right - left) * BINARY_GUIDES[name] * 4
                    expected = b"".join(
                        reference_bytes[
                            row * stride + offset : row * stride + offset + width_bytes
                        ]
                        for row in range(top - ref_top, bottom - ref_top)
                    )
                    actual = chunk_path.read_bytes()
                else:
                    expected = reference_image.crop(
                        (
                            left - ref_left,
                            top - ref_top,
                            right - ref_left,
                            bottom - ref_top,
                        )
                    ).tobytes()
                    with Image.open(chunk_path) as image:
                        actual = image.tobytes()
                if actual != expected:
                    raise ValueError(
                        f"Guide chunk differs from reference pixels: {name}/{frame['id']}"
                    )
        result["categorical_and_numeric_match_reference_exactly"] = True
    (root / "guide-verification.json").write_text(json.dumps(result, indent=2) + "\n")
    return result

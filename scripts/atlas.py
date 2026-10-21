"""Validated fixed-view atlas coordinates and integer-pixel render planning.

All geographic preparation stays in double-precision ENU metres. Atlas pixel
coordinates name pixel edges, not centers; pixel (i, j) samples (i+.5, j+.5).
The inverse requires a known local-ENU up value. It is not roof geocoding.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from geo import local_point

MAX_PIXEL_COORDINATE = 2**31 - 1
SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _vector(value: Any, length: int, name: str) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError(f"{name} must contain {length} numbers")
    return tuple(_number(item, name) for item in value)


def _keys(value: Any, expected: set[str], name: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{name} must contain exactly: {', '.join(sorted(expected))}")


def _slug(value: Any, name: str) -> str:
    if not isinstance(value, str) or not SLUG.fullmatch(value):
        raise ValueError(
            f"{name} must be a lowercase identifier (maximum 96 characters)"
        )
    return value


def _dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _cross(a, b) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _normalized(vector) -> tuple[float, ...]:
    length = math.hypot(*vector)
    if not length:
        raise ValueError("Camera direction cannot be zero")
    return tuple(value / length for value in vector)


def validate_bounds(bounds: Any) -> tuple[int, int, int, int]:
    """Validate a nonempty, half-open integer rectangle, allowing negative origins."""
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
        raise ValueError("bounds_px must contain [left, top, right, bottom]")
    result = tuple(
        _integer(value, "bounds_px", -MAX_PIXEL_COORDINATE, MAX_PIXEL_COORDINATE)
        for value in bounds
    )
    if result[0] >= result[2] or result[1] >= result[3]:
        raise ValueError("bounds_px must be a nonempty half-open rectangle")
    return result


def canonical_json(value: Any) -> str:
    """Stable JSON, with no NaN/Infinity and no whitespace-dependent identity."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class AtlasConfig:
    schema_version: int
    atlas_id: str
    release_id: str
    origin_lonlat: tuple[float, float]
    camera_target_enu: tuple[float, float, float]
    camera_offset_enu: tuple[float, float, float]
    pixel_origin: tuple[float, float]
    pixels_per_metre: float
    bounds_px: tuple[int, int, int, int]
    source_bounds_wsen: tuple[float, float, float, float]
    chunk_size_px: int
    overscan_px: int
    vertical_range_enu_m: tuple[float, float]
    shadow_buffer_m: float
    render_samples: int
    render_seed: int

    def to_dict(self) -> dict:
        """Return JSON-native values, without filesystem paths or mutable internals."""
        return json.loads(canonical_json(asdict(self)))

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(canonical_json(self.to_dict()).encode()).hexdigest()

    @property
    def basis(self) -> dict[str, tuple[float, ...]]:
        """Right, image-up and camera-forward vectors in global local-ENU axes."""
        forward = _normalized(tuple(-value for value in self.camera_offset_enu))
        right = _normalized(_cross(forward, (0.0, 0.0, 1.0)))
        up = _normalized(_cross(right, forward))
        return {"right": right, "up": up, "forward": forward}


def parse_config(value: Any) -> AtlasConfig:
    """Validate the complete v1 contract; reject unknown and missing fields."""
    _keys(value, set(AtlasConfig.__dataclass_fields__), "Atlas configuration")
    version = _integer(value["schema_version"], "schema_version", 1, 1)
    origin = _vector(value["origin_lonlat"], 2, "origin_lonlat")
    if not -180 <= origin[0] <= 180 or not -90 < origin[1] < 90:
        raise ValueError(
            "origin_lonlat must be valid WGS84 coordinates away from poles"
        )
    target = _vector(value["camera_target_enu"], 3, "camera_target_enu")
    offset = _vector(value["camera_offset_enu"], 3, "camera_offset_enu")
    if max(abs(item) for item in target + offset) > 10_000_000:
        raise ValueError("Camera coordinates exceed the local-ENU working range")
    if offset[2] <= 0 or math.hypot(*offset[:2]) <= 1e-9:
        raise ValueError("Camera must look down at an oblique angle")
    if offset[2] / math.hypot(*offset) < 1e-6:
        raise ValueError("Camera elevation is too close to the horizon")
    pixels = _vector(value["pixel_origin"], 2, "pixel_origin")
    if max(abs(item) for item in pixels) > MAX_PIXEL_COORDINATE:
        raise ValueError("pixel_origin exceeds supported coordinates")
    scale = _number(value["pixels_per_metre"], "pixels_per_metre")
    if not 0 < scale <= 10_000:
        raise ValueError("pixels_per_metre must be in (0, 10000]")
    source = _vector(value["source_bounds_wsen"], 4, "source_bounds_wsen")
    if not (-180 <= source[0] < source[2] <= 180 and -90 < source[1] < source[3] < 90):
        raise ValueError(
            "source_bounds_wsen must be nonempty, without antimeridian wrap"
        )
    heights = _vector(value["vertical_range_enu_m"], 2, "vertical_range_enu_m")
    if not -10_000 <= heights[0] < heights[1] <= 100_000:
        raise ValueError("vertical_range_enu_m must be increasing in [-10000, 100000]")
    shadow = _number(value["shadow_buffer_m"], "shadow_buffer_m")
    if not 0 <= shadow <= 100_000:
        raise ValueError("shadow_buffer_m must be in [0, 100000]")
    return AtlasConfig(
        schema_version=version,
        atlas_id=_slug(value["atlas_id"], "atlas_id"),
        release_id=_slug(value["release_id"], "release_id"),
        origin_lonlat=origin,
        camera_target_enu=target,
        camera_offset_enu=offset,
        pixel_origin=pixels,
        pixels_per_metre=scale,
        bounds_px=validate_bounds(value["bounds_px"]),
        source_bounds_wsen=source,
        chunk_size_px=_integer(value["chunk_size_px"], "chunk_size_px", 1, 16384),
        overscan_px=_integer(value["overscan_px"], "overscan_px", 0, 16384),
        vertical_range_enu_m=heights,
        shadow_buffer_m=shadow,
        render_samples=_integer(
            value["render_samples"], "render_samples", 1, 1_000_000
        ),
        render_seed=_integer(value["render_seed"], "render_seed", 0, 2**31 - 1),
    )


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def load_config(path: str | Path) -> AtlasConfig:
    return parse_config(
        json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    )


def world_to_pixel(config: AtlasConfig, point_enu) -> tuple[float, float]:
    """Global ENU metres to global atlas pixel-edge coordinates."""
    point = _vector(point_enu, 3, "point_enu")
    delta = tuple(a - b for a, b in zip(point, config.camera_target_enu, strict=True))
    basis, scale = config.basis, config.pixels_per_metre
    return (
        config.pixel_origin[0] + scale * _dot(delta, basis["right"]),
        config.pixel_origin[1] - scale * _dot(delta, basis["up"]),
    )


def pixel_to_world(
    config: AtlasConfig, pixel, up_m: float
) -> tuple[float, float, float]:
    """Atlas pixel to ENU on a known constant-up plane, not a unique surface hit."""
    pixel = _vector(pixel, 2, "pixel")
    up_m = _number(up_m, "up_m")
    right, up = config.basis["right"], config.basis["up"]
    dz = up_m - config.camera_target_enu[2]
    horizontal = (pixel[0] - config.pixel_origin[0]) / config.pixels_per_metre - right[
        2
    ] * dz
    vertical = (
        -(pixel[1] - config.pixel_origin[1]) / config.pixels_per_metre - up[2] * dz
    )
    determinant = right[0] * up[1] - right[1] * up[0]
    dx = (horizontal * up[1] - right[1] * vertical) / determinant
    dy = (right[0] * vertical - horizontal * up[0]) / determinant
    return (config.camera_target_enu[0] + dx, config.camera_target_enu[1] + dy, up_m)


def geographic_to_pixel(config: AtlasConfig, lon, lat, ellipsoidal_height_m):
    """WGS84 geodetic coordinates to atlas pixels; up is not ellipsoidal height."""
    lon = _number(lon, "longitude")
    lat = _number(lat, "latitude")
    height = _number(ellipsoidal_height_m, "ellipsoidal_height_m")
    if not -180 <= lon <= 180 or not -90 <= lat <= 90:
        raise ValueError("Invalid WGS84 longitude or latitude")
    return world_to_pixel(config, local_point(lon, lat, height, *config.origin_lonlat))


def source_footprint_enu(config: AtlasConfig, bounds_px, buffer_m=None) -> list[float]:
    """Conservative ENU XY box for the given view and configured height interval.

    Includes all pixel corners at both minimum and maximum ENU up, then expands
    by the explicit shadow buffer. This is a source-selection requirement, not
    permission to cut meshes at the box. Heights and shadow reach must be checked
    against actual source geometry and lighting before calling a render complete.
    """
    left, top, right, bottom = validate_bounds(bounds_px)
    buffer_m = (
        config.shadow_buffer_m if buffer_m is None else _number(buffer_m, "buffer_m")
    )
    if buffer_m < 0:
        raise ValueError("buffer_m cannot be negative")
    points = [
        pixel_to_world(config, (x, y), height)
        for x in (left, right)
        for y in (top, bottom)
        for height in config.vertical_range_enu_m
    ]
    return [
        min(point[0] for point in points) - buffer_m,
        min(point[1] for point in points) - buffer_m,
        max(point[0] for point in points) + buffer_m,
        max(point[1] for point in points) + buffer_m,
    ]


def camera_for_bounds(config: AtlasConfig, bounds_px, overscan_px=0) -> dict:
    """Plan a Blender orthographic camera with fixed horizontal sensor fit.

    The core rectangle is integer-aligned and half-open. Expanded rendering is
    cropped in image space. Adjacent camera targets move parallel to the ENU
    plane while preserving the global projection and the camera offset.
    """
    bounds = validate_bounds(bounds_px)
    margin = _integer(overscan_px, "overscan_px", 0, 16384)
    expanded = validate_bounds(
        (bounds[0] - margin, bounds[1] - margin, bounds[2] + margin, bounds[3] + margin)
    )
    width, height = expanded[2] - expanded[0], expanded[3] - expanded[1]
    target = pixel_to_world(
        config,
        ((expanded[0] + expanded[2]) / 2, (expanded[1] + expanded[3]) / 2),
        config.camera_target_enu[2],
    )
    basis = config.basis
    return {
        "bounds_px": list(bounds),
        "render_bounds_px": list(expanded),
        "crop_box_px": [margin, margin, width - margin, height - margin],
        "resolution_px": [width, height],
        "target_enu": list(target),
        "location_enu": [
            a + b for a, b in zip(target, config.camera_offset_enu, strict=True)
        ],
        "right_enu": list(basis["right"]),
        "up_enu": list(basis["up"]),
        "forward_enu": list(basis["forward"]),
        "ortho_scale_m": width / config.pixels_per_metre,
        "sensor_fit": "HORIZONTAL",
        "source_footprint_enu_m": source_footprint_enu(config, expanded),
    }


def job_fingerprint(config: AtlasConfig, bounds_px, input_hashes, tool_versions) -> str:
    """Hash configuration, exact core bounds, source content and tool identities.

    Keys are stable logical identifiers, never filenames. A configuration alone
    cannot establish reproducibility: callers supply checked source hashes and
    relevant renderer/tool versions before starting production work.
    """
    if not isinstance(input_hashes, dict) or not input_hashes:
        raise ValueError("input_hashes must contain at least one source SHA256")
    for key, value in input_hashes.items():
        _slug(key, "input hash identifier")
        if not isinstance(value, str) or not SHA256.fullmatch(value):
            raise ValueError("Input hashes must be lowercase SHA256 hex strings")
    if not isinstance(tool_versions, dict) or not tool_versions:
        raise ValueError("tool_versions must contain at least one tool version")
    for key, value in tool_versions.items():
        _slug(key, "tool identifier")
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 128
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError(
                "Tool versions must be nonempty strings up to 128 characters"
            )
    payload = {
        "config": config.to_dict(),
        "bounds_px": list(validate_bounds(bounds_px)),
        "input_hashes": input_hashes,
        "tool_versions": tool_versions,
    }
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def plan_chunks(config: AtlasConfig, *, max_chunks: int = 100_000) -> dict:
    """Plan row-major chunks anchored to the global zero-pixel grid.

    Edge chunks are clipped to the requested atlas extent, not shifted off-grid.
    Source coverage is not inferred from successful planning. Repeating this on
    a neighboring extent retains the same global chunk identifiers and pixels.
    """
    _integer(max_chunks, "max_chunks", 1, 1_000_000)
    left, top, right, bottom = config.bounds_px
    size = config.chunk_size_px
    x0, x1 = left // size, (right - 1) // size
    y0, y1 = top // size, (bottom - 1) // size
    count = (x1 - x0 + 1) * (y1 - y0 + 1)
    if count > max_chunks:
        raise ValueError(f"Plan has {count} chunks, exceeding the limit {max_chunks}")
    chunks = []
    for row in range(y0, y1 + 1):
        for column in range(x0, x1 + 1):
            bounds = (
                max(left, column * size),
                max(top, row * size),
                min(right, (column + 1) * size),
                min(bottom, (row + 1) * size),
            )
            chunks.append(
                {
                    "id": f"x{column}_y{row}",
                    **camera_for_bounds(config, bounds, config.overscan_px),
                }
            )
    return {
        "schema_version": 1,
        "atlas_id": config.atlas_id,
        "release_id": config.release_id,
        "config_sha256": config.fingerprint,
        "bounds_px": list(config.bounds_px),
        "chunk_size_px": size,
        "overscan_px": config.overscan_px,
        "chunk_count": count,
        "source_coverage_verified": False,
        "chunks": chunks,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "plan"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-chunks", type=int, default=100_000)
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        result = (
            {
                "valid": True,
                "atlas_id": config.atlas_id,
                "config_sha256": config.fingerprint,
            }
            if args.command == "validate"
            else plan_chunks(config, max_chunks=args.max_chunks)
        )
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    content = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content, end="")


if __name__ == "__main__":
    main()

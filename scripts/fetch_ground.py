"""Download and stitch a small official PLATEAU orthophoto XYZ mosaic."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

DEFAULT_BBOX = (139.6873, 35.6860, 139.6961, 35.6932)
TILE_SIZE = 256
EARTH_RADIUS = 6378137.0
MAX_LATITUDE = 85.0511287798066
SOURCE = "https://tile.plateauview.mlit.go.jp/tiles/plateau-ortho-2023/{z}/{x}/{y}.png"
DOCUMENTATION = "https://docs.plateauview.mlit.go.jp/datasets/ortho/"


def lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    """Return fractional XYZ tile coordinates, clamping Mercator latitudes."""
    if not math.isfinite(lon) or not math.isfinite(lat):
        raise ValueError("Coordinates must be finite")
    if not -180 <= lon <= 180:
        raise ValueError("Longitude must be in [-180, 180]")
    if type(zoom) is not int or not 0 <= zoom <= 30:
        raise ValueError("Zoom must be in [0, 30]")
    lat = min(MAX_LATITUDE, max(-MAX_LATITUDE, lat))
    scale = 2**zoom
    radians = math.radians(lat)
    return (
        (lon + 180.0) / 360.0 * scale,
        (1.0 - math.asinh(math.tan(radians)) / math.pi) / 2.0 * scale,
    )


def tile_to_lonlat(x: float, y: float, zoom: int) -> tuple[float, float]:
    """Return longitude and latitude of an XYZ tile-grid corner."""
    scale = 2**zoom
    return (
        x / scale * 360.0 - 180.0,
        math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / scale)))),
    )


def tile_bounds(x: int, y: int, zoom: int) -> tuple[float, float, float, float]:
    """Return WGS84 west, south, east, north bounds of one tile."""
    west, north = tile_to_lonlat(x, y, zoom)
    east, south = tile_to_lonlat(x + 1, y + 1, zoom)
    return west, south, east, north


def tile_range(bbox: tuple[float, ...], zoom: int) -> tuple[int, int, int, int]:
    """Return inclusive xmin, ymin, xmax, ymax intersecting a WGS84 box."""
    west, south, east, north = bbox
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("Expected nonempty west, south, east, north bounds")
    x0, y0 = lonlat_to_tile(west, north, zoom)
    x1, y1 = lonlat_to_tile(east, south, zoom)
    max_index = 2**zoom - 1
    return (
        max(0, min(max_index, math.floor(x0))),
        max(0, min(max_index, math.floor(y0))),
        max(0, min(max_index, math.ceil(x1) - 1)),
        max(0, min(max_index, math.ceil(y1) - 1)),
    )


def mercator(lon: float, lat: float) -> tuple[float, float]:
    """Convert a geographic corner to EPSG:3857 metres for exact UV mapping."""
    return (
        EARTH_RADIUS * math.radians(lon),
        EARTH_RADIUS * math.asinh(math.tan(math.radians(lat))),
    )


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Orthophoto redirects require an explicit source adapter")


class DownloadBudget:
    """Shared hard limits for actual response bodies and retained source bytes.

    Reads are serialized under a lock so concurrent workers cannot overspend.
    Response headers and transport overhead are not included in these limits.
    """

    def __init__(self, max_bytes=50_000_000, *, opener=None):
        if type(max_bytes) is not int or not 1 <= max_bytes <= 100_000_000:
            raise ValueError("Ground byte budget must be in [1,100000000]")
        self.max_bytes = max_bytes
        self.network_bytes = self.source_bytes = 0
        self.lock = threading.Lock()
        self.opener = opener or build_opener(NoRedirect()).open

    def fetch(self, url):
        with self.lock:
            with self.opener(
                Request(url, headers={"User-Agent": "isometric-tokyo-ground/2"}),
                timeout=45,
            ) as response:
                expected = response.headers.get("Content-Length")
                expected = int(expected) if expected is not None else None
                remaining = min(
                    self.max_bytes - self.network_bytes,
                    self.max_bytes - self.source_bytes,
                    1_000_000,
                )
                if expected is not None and not 0 <= expected <= remaining:
                    raise ValueError(
                        "Orthophoto response exceeds remaining byte budget"
                    )
                parts, size = [], 0
                while expected is None or size < expected:
                    remaining = min(
                        self.max_bytes - self.network_bytes,
                        self.max_bytes - self.source_bytes - size,
                        1_000_000 - size,
                    )
                    if remaining <= 0:
                        raise ValueError(
                            "Orthophoto byte budget exhausted before confirmed EOF"
                        )
                    part = response.read(
                        min(
                            65536,
                            remaining,
                            expected - size if expected is not None else remaining,
                        )
                    )
                    self.network_bytes += len(part)
                    size += len(part)
                    if not part:
                        if expected is not None and size != expected:
                            raise URLError("Truncated orthophoto response")
                        break
                    parts.append(part)
                return b"".join(parts)

    def retain(self, size):
        with self.lock:
            if self.source_bytes + size > self.max_bytes:
                raise ValueError("Cached orthophoto exceeds source byte budget")
            self.source_bytes += size


def download_tile(task: tuple[int, int, int, Path]) -> dict:
    from PIL import Image

    x, y, zoom, output, *options = task
    budget = options[0] if options else DownloadBudget()
    relative = f"tiles/{zoom}/{x}/{y}.png"
    path = output / relative
    url = SOURCE.format(z=zoom, x=x, y=y)
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path = path.with_suffix(".json")
    if path.exists() and path.stat().st_size > 1_000_000:
        raise ValueError("Cached orthophoto exceeds per-tile byte budget")
    if path.exists() and metadata_path.exists():
        metadata = json.loads(metadata_path.read_text())
        if (
            metadata.get("url") != url
            or path.stat().st_size != metadata.get("bytes")
            or hashlib.sha256(path.read_bytes()).hexdigest() != metadata.get("sha256")
        ):
            raise ValueError("Cached orthophoto checksum mismatch")
    else:
        for attempt in range(3):
            try:
                payload = budget.fetch(url)
                temporary = path.with_suffix(".tmp")
                temporary.write_bytes(payload)
                with Image.open(temporary) as tile:
                    tile.load()
                    if tile.size != (TILE_SIZE, TILE_SIZE):
                        raise ValueError(
                            f"Unexpected tile dimensions from {url}: {tile.size}"
                        )
                temporary.replace(path)
                metadata_path.write_text(
                    json.dumps(
                        {
                            "url": url,
                            "bytes": len(payload),
                            "sha256": hashlib.sha256(payload).hexdigest(),
                        }
                    )
                    + "\n"
                )
                break
            except (URLError, TimeoutError) as exc:
                if attempt == 2 or (
                    isinstance(exc, HTTPError) and exc.code != 429 and exc.code < 500
                ):
                    raise
                time.sleep(2**attempt)
    with Image.open(path) as tile:
        if tile.size != (TILE_SIZE, TILE_SIZE):
            raise ValueError(f"Invalid cached tile dimensions: {path}")
        tile.verify()
    budget.retain(path.stat().st_size)
    payload = path.read_bytes()
    return {
        "x": x,
        "y": y,
        "z": zoom,
        "path": relative,
        "url": url,
        "bounds_wsen": tile_bounds(x, y, zoom),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        default=DEFAULT_BBOX,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
    )
    parser.add_argument("--zoom", type=int, default=18, choices=range(10, 20))
    parser.add_argument("--output", type=Path, default=Path("data/ground"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-bytes", type=int, default=50_000_000)
    args = parser.parse_args()
    budget = DownloadBudget(args.max_bytes)
    if not 1 <= args.workers <= 16:
        parser.error("--workers must be in [1,16]")
    xmin, ymin, xmax, ymax = tile_range(tuple(args.bbox), args.zoom)
    count = (xmax - xmin + 1) * (ymax - ymin + 1)
    if count > 400:
        parser.error(
            f"Refusing {count} tiles: this sample downloader is limited to 400"
        )
    tasks = [
        (x, y, args.zoom, args.output, budget)
        for y in range(ymin, ymax + 1)
        for x in range(xmin, xmax + 1)
    ]
    print(
        f"Downloading/verifying {count} PLATEAU tiles at zoom {args.zoom}", flush=True
    )
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        tiles = list(executor.map(download_tile, tasks))

    from PIL import Image

    width = (xmax - xmin + 1) * TILE_SIZE
    height = (ymax - ymin + 1) * TILE_SIZE
    mosaic = Image.new("RGB", (width, height))
    for tile in tiles:
        col, row = tile["x"] - xmin, tile["y"] - ymin
        tile["column"] = col
        tile["row"] = row
        tile["pixel_offset_xy"] = [col * TILE_SIZE, row * TILE_SIZE]
        with Image.open(args.output / tile["path"]) as source:
            mosaic.paste(source.convert("RGB"), tuple(tile["pixel_offset_xy"]))
    mosaic.save(args.output / "ground.png")
    west, north = tile_to_lonlat(xmin, ymin, args.zoom)
    east, south = tile_to_lonlat(xmax + 1, ymax + 1, args.zoom)
    min_x, min_y = mercator(west, south)
    max_x, max_y = mercator(east, north)
    manifest = {
        "source": "PLATEAU-Ortho, 2023 edition",
        "source_url_template": SOURCE,
        "documentation_url": DOCUMENTATION,
        "attribution": "Source: Project PLATEAU, Ministry of Land, Infrastructure, Transport and Tourism (MLIT), Japan. Processed into a local mosaic.",
        "license_note": "PLATEAU distribution documentation permits reuse under CC BY 4.0-compatible terms; see source policy and third-party notices. The orthophoto service can combine PLATEAU and GSI imagery.",
        "license_url": "https://www.mlit.go.jp/plateau/site-policy/",
        "created_utc": datetime.now(UTC).isoformat(),
        "requested_bbox_wsen": list(args.bbox),
        "bounds_wsen": [west, south, east, north],
        "bounds_epsg3857": [min_x, min_y, max_x, max_y],
        "image": "ground.png",
        "image_width": width,
        "image_height": height,
        "image_origin": "northwest; columns eastward and rows southward",
        "zoom": args.zoom,
        "tile_size": TILE_SIZE,
        "tile_count": count,
        "tile_range": [xmin, ymin, xmax, ymax],
        "total_download_bytes": sum(tile["bytes"] for tile in tiles),
        "actual_network_bytes": budget.network_bytes,
        "byte_budget": budget.max_bytes,
        "image_sha256": hashlib.sha256(
            (args.output / "ground.png").read_bytes()
        ).hexdigest(),
        "source_coverage_verified": False,
        "tiles": tiles,
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Saved {width}x{height} mosaic and manifest to {args.output}", flush=True)


if __name__ == "__main__":
    main()

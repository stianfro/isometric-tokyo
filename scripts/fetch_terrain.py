"""Download a small official PLATEAU ellipsoidal-height terrain mesh."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import struct
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

LAYER_URL = "https://tile.plateauview.mlit.go.jp/terrain/layer.json"
DEFAULT_BBOX = (139.6873, 35.6860, 139.6961, 35.6932)


def tile_bounds(x: int, y: int, zoom: int) -> tuple[float, float, float, float]:
    """Return geographic bounds for a TMS geodetic (2x1 root) tile."""
    span = 180 / 2**zoom
    return (
        -180 + x * span,
        -90 + y * span,
        -180 + (x + 1) * span,
        -90 + (y + 1) * span,
    )


def tile_range(bbox: tuple[float, ...], zoom: int) -> list[tuple[int, int]]:
    west, south, east, north = bbox
    if not -180 <= west < east <= 180 or not -90 <= south < north <= 90:
        raise ValueError("Expected a nonempty west, south, east, north bounding box")
    span = 180 / 2**zoom
    return [
        (x, y)
        for y in range(math.floor((south + 90) / span), math.ceil((north + 90) / span))
        for x in range(math.floor((west + 180) / span), math.ceil((east + 180) / span))
    ]


def decode_delta(values: tuple[int, ...]) -> list[int]:
    total = 0
    result = []
    for value in values:
        total += (value >> 1) ^ -(value & 1)
        if not 0 <= total <= 32767:
            raise ValueError("Quantized coordinate out of bounds")
        result.append(total)
    return result


def decode_mesh(payload: bytes, bounds: tuple[float, ...]) -> tuple[list, list]:
    """Decode quantized-mesh core geometry, ignoring optional normal extensions."""
    if payload.startswith(b"\x1f\x8b"):
        payload = gzip.decompress(payload)
    if len(payload) < 92:
        raise ValueError("Truncated quantized-mesh header")
    min_height, max_height = struct.unpack_from("<ff", payload, 24)
    count = struct.unpack_from("<I", payload, 88)[0]
    if not count or count > 2_000_000 or len(payload) < 92 + count * 6 + 4:
        raise ValueError("Invalid or truncated quantized-mesh vertex array")
    offset = 92
    channels = []
    for _ in range(3):
        channels.append(decode_delta(struct.unpack_from(f"<{count}H", payload, offset)))
        offset += count * 2
    west, south, east, north = bounds
    vertices = [
        [
            west + u / 32767 * (east - west),
            south + v / 32767 * (north - south),
            min_height + h / 32767 * (max_height - min_height),
        ]
        for u, v, h in zip(*channels, strict=True)
    ]
    index_size = 4 if count > 65536 else 2
    offset += (-offset) % index_size
    if len(payload) < offset + 4:
        raise ValueError("Missing triangle count")
    triangle_count = struct.unpack_from("<I", payload, offset)[0]
    offset += 4
    if len(payload) < offset + triangle_count * 3 * index_size:
        raise ValueError("Truncated triangle index array")
    fmt = "I" if index_size == 4 else "H"
    codes = struct.unpack_from(f"<{triangle_count * 3}{fmt}", payload, offset)
    highest = 0
    indices = []
    for code in codes:
        index = highest - code
        if not 0 <= index < count:
            raise ValueError("Invalid high-water triangle index")
        indices.append(index)
        if code == 0:
            highest += 1
    faces = [indices[i : i + 3] for i in range(0, len(indices), 3)]
    return vertices, faces


def fetch(url: str, path: Path) -> bytes:
    if not path.exists():
        request = Request(
            url,
            headers={
                "Accept": "application/vnd.quantized-mesh,application/json",
                "User-Agent": "ShinjukuSourceStudy/1.0",
            },
        )
        with urlopen(request, timeout=40) as response:
            payload = response.read()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return path.read_bytes()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/terrain"))
    parser.add_argument("--bbox", type=float, nargs=4, default=DEFAULT_BBOX)
    parser.add_argument("--zoom", type=int, default=14, choices=range(19))
    args = parser.parse_args()
    output = args.output
    layer = json.loads(fetch(LAYER_URL, output / "layer.json"))
    if layer.get("projection") != "EPSG:4326" or layer.get("scheme") != "tms":
        raise ValueError("Expected TMS geodetic tiles")
    if layer.get("format") != "quantized-mesh-1.0" or args.zoom > layer["maxzoom"]:
        raise ValueError("Unsupported terrain format or zoom")
    tiles = tile_range(tuple(args.bbox), args.zoom)
    if len(tiles) > 16:
        raise ValueError("Terrain sample is limited to 16 tiles")
    vertices, faces, tile_metadata = [], [], []
    for x, y in tiles:
        relative = f"tiles/{args.zoom}/{x}/{y}.terrain"
        url = urljoin(
            LAYER_URL,
            layer["tiles"][0].format(z=args.zoom, x=x, y=y, version=layer["version"]),
        )
        payload = fetch(url, output / relative)
        bounds = tile_bounds(x, y, args.zoom)
        tile_vertices, tile_faces = decode_mesh(payload, bounds)
        start = len(vertices)
        vertices.extend(tile_vertices)
        faces.extend([[index + start for index in face] for face in tile_faces])
        tile_metadata.append(
            {
                "url": url,
                "path": relative,
                "bounds_wsen": bounds,
                "vertex_count": len(tile_vertices),
                "triangle_count": len(tile_faces),
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
        print(
            f"Decoded {url}: {len(tile_vertices)} vertices, {len(tile_faces)} triangles",
            flush=True,
        )
    mesh = {
        "source": "PLATEAU-Terrain",
        "source_url": LAYER_URL,
        "documentation_url": "https://docs.plateauview.mlit.go.jp/datasets/terrain/",
        "attribution": "PLATEAU | Mapterhorn | 国土地理院",
        "license_url": "https://www.mlit.go.jp/plateau/site-policy/",
        "processing": "Quantized-mesh core decoded to geographic vertices; optional normals omitted. Full intersecting terrain tiles retained.",
        "height_reference": "WGS84 ellipsoidal height in metres, geoid correction already applied by source",
        "requested_bbox_wsen": args.bbox,
        "zoom": args.zoom,
        "height_range_m": [min(v[2] for v in vertices), max(v[2] for v in vertices)],
        "vertices_lon_lat_height": vertices,
        "triangles": faces,
        "tiles": tile_metadata,
    }
    (output / "mesh.json").write_text(
        json.dumps(mesh, separators=(",", ":"), ensure_ascii=False) + "\n"
    )
    print(
        f"Saved {len(vertices)} vertices, heights {mesh['height_range_m']} to {output / 'mesh.json'}"
    )


if __name__ == "__main__":
    main()

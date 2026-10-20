"""Download a bounded, highest-detail PLATEAU building sample, without AI.

Retains original b3dm files and extracts their embedded GLBs losslessly. The
manifest preserves 3D Tiles transforms and both kinds of relative-to-center
metadata; it does not bake coordinate transforms into the geometry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

SOURCE_URL = (
    "https://assets.cms.plateau.reearth.io/assets/00/"
    "bed0bd-f882-4cde-b942-21d0f8d2ddc2/"
    "13104_shinjuku-ku_pref_2025_citygml_1_op_bldg_3dtiles_"
    "13104_shinjuku-ku_lod2/tileset.json"
)
IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
DEFAULT_BBOX = [139.6873, 35.6860, 139.6961, 35.6932]


def matmul(a: list[float], b: list[float]) -> list[float]:
    """Multiply two 4x4 column-major matrices (parent times child)."""
    if len(a) != 16 or len(b) != 16:
        raise ValueError("A transform must contain exactly 16 values")
    return [
        sum(a[k * 4 + row] * b[col * 4 + k] for k in range(4))
        for col in range(4)
        for row in range(4)
    ]


def region_intersects(region: list[float], bbox: list[float]) -> bool:
    """Intersection for a non-antimeridian geographic bbox in degrees.

    3D Tiles regions are radians in WGS84 and are not changed by tile transforms.
    This intentionally rejects wrapped regions rather than silently misselecting.
    """
    west, south, east, north = map(math.degrees, region[:4])
    if west > east or bbox[0] > bbox[2]:
        raise ValueError("Antimeridian-crossing bounds are not supported")
    return not (east < bbox[0] or west > bbox[2] or north < bbox[1] or south > bbox[3])


def select_leaf_tiles(tileset: dict, bbox: list[float]) -> list[dict]:
    """Select intersecting terminal contents, excluding coarse REPLACE parents.

    The Shinjuku source has explicit region bounds and terminal b3dm contents.
    Unknown bounding-volume schemes or external tilesets fail explicitly.
    """
    selected: list[dict] = []

    def visit(node: dict, parent_transform: list[float], refine: str) -> None:
        transform = matmul(parent_transform, node.get("transform", IDENTITY))
        region = node.get("boundingVolume", {}).get("region")
        if region is None:
            raise ValueError("This sampler requires explicit region bounds")
        if not region_intersects(region, bbox):
            return
        children = node.get("children", [])
        refine = node.get("refine", refine)
        content = node.get("content")
        if content and (not children or refine == "ADD"):
            uri = content.get("uri", content.get("url"))
            if not uri:
                raise ValueError("Tile content is missing its URI")
            if not urlparse(uri).path.lower().endswith((".b3dm", ".glb")):
                raise ValueError(f"Unsupported content or external tileset: {uri}")
            bounds = content.get("boundingVolume", node["boundingVolume"])
            if "region" not in bounds:
                raise ValueError("This sampler requires content region bounds")
            if region_intersects(bounds["region"], bbox):
                selected.append(
                    {
                        "uri": uri,
                        "transform": transform,
                        "bounding_volume": bounds,
                        "tile_bounding_volume": node["boundingVolume"],
                        "geometric_error": node.get("geometricError"),
                    }
                )
        for child in children:
            visit(child, transform, refine)

    visit(tileset["root"], IDENTITY, "REPLACE")
    return sorted(selected, key=lambda tile: tile["uri"])


def unpack_b3dm(blob: bytes) -> tuple[bytes, dict, dict]:
    """Return the original embedded GLB, feature table, and batch JSON."""
    if len(blob) < 28:
        raise ValueError("Truncated b3dm header")
    magic, version, length, ftj, ftb, btj, btb = struct.unpack_from("<4s6I", blob)
    if magic != b"b3dm" or version != 1 or length != len(blob):
        raise ValueError("Invalid b3dm header, version, or byteLength")
    glb_offset = 28 + ftj + ftb + btj + btb
    if glb_offset + 12 > len(blob):
        raise ValueError("Truncated b3dm tables or embedded GLB")
    feature = json.loads(blob[28 : 28 + ftj].rstrip(b" \x00")) if ftj else {}
    batch_start = 28 + ftj + ftb
    batch = (
        json.loads(blob[batch_start : batch_start + btj].rstrip(b" \x00"))
        if btj
        else {}
    )
    if isinstance(feature.get("RTC_CENTER"), dict):
        offset = feature["RTC_CENTER"].get("byteOffset")
        if not isinstance(offset, int) or offset < 0 or offset + 12 > ftb:
            raise ValueError("Invalid binary RTC_CENTER offset")
        feature["RTC_CENTER"] = list(struct.unpack_from("<3f", blob, 28 + ftj + offset))
    return blob[glb_offset:], feature, batch


def glb_json(blob: bytes) -> dict:
    """Read the JSON chunk without decoding meshes or changing binary assets."""
    if len(blob) < 20:
        raise ValueError("Truncated GLB")
    magic, version, length = struct.unpack_from("<4s2I", blob)
    if magic != b"glTF" or version != 2 or length != len(blob):
        raise ValueError("Invalid GLB header, version, or byteLength")
    json_length, chunk_type = struct.unpack_from("<2I", blob, 12)
    if chunk_type != 0x4E4F534A or 20 + json_length > len(blob):
        raise ValueError("Invalid GLB JSON chunk")
    return json.loads(blob[20 : 20 + json_length].rstrip(b" \x00"))


def batch_scalar_values(blob: bytes, key: str) -> list:
    """Read a scalar batch property from JSON or the b3dm binary batch table."""
    _, feature, batch = unpack_b3dm(blob)
    value = batch.get(key, [])
    if isinstance(value, list):
        return value
    if not isinstance(value, dict) or value.get("type") != "SCALAR":
        raise ValueError(f"Unsupported scalar batch property: {key}")
    formats = {
        "BYTE": "b",
        "UNSIGNED_BYTE": "B",
        "SHORT": "h",
        "UNSIGNED_SHORT": "H",
        "INT": "i",
        "UNSIGNED_INT": "I",
        "FLOAT": "f",
        "DOUBLE": "d",
    }
    component = formats.get(value.get("componentType"))
    count = feature.get("BATCH_LENGTH")
    offset = value.get("byteOffset")
    if component is None or not isinstance(count, int) or count < 0:
        raise ValueError("Invalid batch component type or BATCH_LENGTH")
    _, _, _, ftj, ftb, btj, btb = struct.unpack_from("<4s6I", blob)
    size = struct.calcsize(component) * count
    if not isinstance(offset, int) or offset < 0 or offset + size > btb:
        raise ValueError("Batch scalar values exceed binary table bounds")
    return list(
        struct.unpack_from("<" + component * count, blob, 28 + ftj + ftb + btj + offset)
    )


class Downloader:
    """Cache public HTTPS resources with a hard total input-byte limit."""

    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        self.total_bytes = 0

    def fetch(self, url: str, path: Path) -> bytes:
        if urlparse(url).scheme != "https":
            raise ValueError("Only HTTPS data sources are accepted")
        if path.exists():
            blob = path.read_bytes()
            if self.total_bytes + len(blob) > self.max_bytes:
                raise ValueError("Cached input exceeds the sample download budget")
            self.total_bytes += len(blob)
            return blob
        path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(3):
            try:
                request = Request(
                    url, headers={"User-Agent": "PLATEAU-Shinjuku-source-study/1.0"}
                )
                with urlopen(request, timeout=60) as response:
                    expected = int(response.headers.get("Content-Length", "0"))
                    if self.total_bytes + expected > self.max_bytes:
                        raise ValueError("Download would exceed the sample byte budget")
                    chunks: list[bytes] = []
                    size = 0
                    while chunk := response.read(1024 * 1024):
                        size += len(chunk)
                        if self.total_bytes + size > self.max_bytes:
                            raise ValueError("Download exceeds the sample byte budget")
                        chunks.append(chunk)
                    blob = b"".join(chunks)
                partial = path.with_suffix(path.suffix + ".part")
                partial.write_bytes(blob)
                partial.replace(path)
                self.total_bytes += len(blob)
                return blob
            except (OSError, TimeoutError):
                if attempt == 2:
                    raise
                time.sleep(2**attempt)
        raise AssertionError("Unreachable retry state")


def download_sample(
    output: Path,
    bbox: list[float],
    origin: list[float],
    source_url: str,
    max_bytes: int,
) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    downloader = Downloader(max_bytes)
    source_name = hashlib.sha256(source_url.encode()).hexdigest()[:12]
    metadata_path = output / "metadata" / f"tileset-{source_name}.json"
    metadata = downloader.fetch(source_url, metadata_path)
    tileset = json.loads(metadata)
    selected = select_leaf_tiles(tileset, bbox)
    if not selected:
        raise ValueError(
            "No highest-detail building tiles intersect the requested bounds"
        )
    tiles = []
    for index, tile in enumerate(selected, 1):
        url = urljoin(source_url, tile["uri"])
        name = Path(urlparse(url).path).name
        prefix = hashlib.sha256(url.encode()).hexdigest()[:12]
        relative_path = Path("tiles") / f"{prefix}-{name}"
        original = downloader.fetch(url, output / relative_path)
        if name.lower().endswith(".b3dm"):
            glb, feature, batch = unpack_b3dm(original)
            lod_counts = dict(
                Counter(str(lod) for lod in batch_scalar_values(original, "_lod"))
            )
        else:
            glb, feature, batch = original, {}, {}
            lod_counts = {}
        gltf = glb_json(glb)
        glb_path = Path("glb") / f"{prefix}-{Path(name).stem}.glb"
        (output / glb_path).parent.mkdir(parents=True, exist_ok=True)
        (output / glb_path).write_bytes(glb)
        dependencies = []
        for resource in gltf.get("buffers", []) + gltf.get("images", []):
            uri = resource.get("uri")
            if not uri or uri.startswith("data:"):
                continue
            parsed = urlparse(uri)
            dependency = Path(uri)
            if (
                parsed.scheme
                or parsed.netloc
                or dependency.is_absolute()
                or ".." in dependency.parts
            ):
                raise ValueError(f"Unsupported external GLB dependency: {uri}")
            dep_path = glb_path.parent / dependency
            downloader.fetch(urljoin(url, uri), output / dep_path)
            dependencies.append(dep_path.as_posix())
        feature_rtc = feature.get("RTC_CENTER")
        cesium_rtc = gltf.get("extensions", {}).get("CESIUM_RTC", {}).get("center")
        if feature_rtc is not None and cesium_rtc is not None:
            raise ValueError(
                "Both feature-table RTC and CESIUM_RTC occur; resolve explicitly"
            )
        dates = batch.get("core:creationDate", [])
        tiles.append(
            {
                **tile,
                "url": url,
                "path": relative_path.as_posix(),
                "glb_path": glb_path.as_posix(),
                "sha256": hashlib.sha256(original).hexdigest(),
                "bytes": len(original),
                "glb_bytes": len(glb),
                "rtc_center": feature_rtc or cesium_rtc or [0, 0, 0],
                "rtc_source": "feature_table.RTC_CENTER"
                if feature_rtc is not None
                else (
                    "gltf.extensions.CESIUM_RTC.center"
                    if cesium_rtc is not None
                    else "none"
                ),
                "feature_table": feature,
                "gltf_extensions_used": gltf.get("extensionsUsed", []),
                "gltf_extensions_required": gltf.get("extensionsRequired", []),
                "gltf_node_transforms": [
                    {
                        key: node[key]
                        for key in ("matrix", "translation", "rotation", "scale")
                        if key in node
                    }
                    for node in gltf.get("nodes", [])
                ],
                "building_count": feature.get("BATCH_LENGTH"),
                "lod_counts": lod_counts,
                "source_creation_dates": sorted({str(date) for date in dates if date}),
                "source_survey_years": sorted(
                    {
                        str(year)
                        for year in batch.get(
                            "uro:BuildingDetailAttribute_uro:surveyYear", []
                        )
                        if year
                    }
                ),
                "building_names": sorted(
                    {str(name) for name in batch.get("gml:name", []) if name}
                ),
                "dependencies": dependencies,
            }
        )
        print(f"[{index}/{len(selected)}] {name}: {len(original):,} bytes", flush=True)
    manifest = {
        "schema_version": 1,
        "title": "Shinjuku PLATEAU source-quality sample, unstyled building geometry",
        "source_url": source_url,
        "source_metadata_path": metadata_path.relative_to(output).as_posix(),
        "source_metadata_sha256": hashlib.sha256(metadata).hexdigest(),
        "source_date": "2025 dataset release label; acquisition date not asserted",
        "fetched_at": datetime.now(UTC).isoformat(),
        "attribution": "Source: Project PLATEAU, Ministry of Land, Infrastructure, Transport and Tourism, Japan; Shinjuku-ku 2025 3D city model. Sample selected and rendered from source data.",
        "license": {
            "name": "PLATEAU open-data terms; see source policy and individual dataset notices",
            "url": "https://www.mlit.go.jp/plateau/site-policy/",
            "note": "Retain attribution and indicate modifications. No Google imagery or AI styling used.",
        },
        "bbox": bbox,
        "bbox_order": "west,south,east,north in WGS84 degrees",
        "origin": {"lat": origin[0], "lon": origin[1], "height": origin[2]},
        "coordinate_convention": {
            "transform": "column-major 4x4 accumulated parent * child tile transform",
            "rtc_center": "ECEF meters; add after glTF Y-up to 3D Tiles Z-up conversion, before tile transform",
            "local_axes_after_enu": "x east, y north, z up, meters",
            "gltf_nodes": "Node transforms remain embedded in GLB and must be applied by importer",
        },
        "selection": "Highest-detail terminal tiles whose content region intersects bbox. Whole tiles retained; geometry may extend beyond requested bbox.",
        "tile_count": len(tiles),
        "building_count": sum(tile["building_count"] or 0 for tile in tiles),
        "lod_counts": dict(
            sum((Counter(tile["lod_counts"]) for tile in tiles), Counter())
        ),
        "download_bytes": downloader.total_bytes,
        "tiles": tiles,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        f"Saved {len(tiles)} tiles, {downloader.total_bytes:,} source bytes to {output}"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/sample"))
    parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        default=DEFAULT_BBOX,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
    )
    parser.add_argument(
        "--origin",
        type=float,
        nargs=3,
        default=[35.6896, 139.6917, 0],
        metavar=("LAT", "LON", "HEIGHT"),
    )
    parser.add_argument("--source-url", default=SOURCE_URL)
    parser.add_argument("--max-mb", type=float, default=150)
    args = parser.parse_args()
    west, south, east, north = args.bbox
    if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
        parser.error("bbox must be ordered west, south, east, north in valid degrees")
    if args.max_mb <= 0:
        parser.error("--max-mb must be positive")
    download_sample(
        args.output,
        args.bbox,
        args.origin,
        args.source_url,
        int(args.max_mb * 1024 * 1024),
    )


if __name__ == "__main__":
    main()

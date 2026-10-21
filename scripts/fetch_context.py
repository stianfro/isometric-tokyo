"""Fetch a strictly bounded PLATEAU vegetation, road or water context sample.

Uses verified catalog items, preserves raw metadata and feature tables, and never
substitutes building-specific attribution or assumes that listed coverage exists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from atlas import load_config
from fetch_sample import batch_scalar_values, glb_json, select_leaf_tiles, unpack_b3dm

MAX_BUDGET_BYTES = 25_000_000
MODEL_TYPES = {"veg", "tran", "wtr"}
SOURCE_HOSTS = {"assets.cms.plateau.reearth.io"}
MARKER = ".plateau-context"


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )


def validate_url(url):
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in SOURCE_HOSTS
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.query
        or parsed.port not in (None, 443)
    ):
        raise ValueError(
            "Context assets must use the confirmed public PLATEAU asset origin"
        )
    if unquote(parsed.path).lower().endswith((".zip", ".7z")):
        raise ValueError("Whole-model archives are outside the context sampler")
    return url


class NoRedirect(HTTPRedirectHandler):
    """Do not silently leave a catalog asset URL or bypass request accounting."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Context asset redirects require an explicit source adapter")


class BoundedCache:
    """Checksummed URL cache with separate hard source and actual-network budgets."""

    def __init__(self, root, max_bytes=MAX_BUDGET_BYTES, *, opener=None):
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BUDGET_BYTES:
            raise ValueError("Context byte budget must be in [1,25000000]")
        self.root, self.max_bytes = Path(root), max_bytes
        self.opener = opener if opener is not None else build_opener(NoRedirect()).open
        self.network_bytes, self.source_bytes, self.requests = 0, 0, 0
        self.seen = set()

    def fetch(self, url):
        validate_url(url)
        key = digest(url.encode())
        raw_path, meta_path = self.root / (key + ".bin"), self.root / (key + ".json")
        if raw_path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text())
            if meta.get("url") != url or meta.get("bytes") != raw_path.stat().st_size:
                raise ValueError("Context cache metadata mismatch")
            size = raw_path.stat().st_size
            if key not in self.seen and self.source_bytes + size > self.max_bytes:
                raise ValueError("Cached source exceeds context byte budget")
            blob = raw_path.read_bytes()
            if meta.get("sha256") != digest(blob):
                raise ValueError("Context cache checksum mismatch")
        else:
            self.root.mkdir(parents=True, exist_ok=True)
            for attempt in range(2):
                if self.requests >= 128:
                    raise ValueError("Context request budget exhausted")
                self.requests += 1
                try:
                    request = Request(
                        url, headers={"User-Agent": "isometric-tokyo-context/1"}
                    )
                    with self.opener(request, timeout=45) as response:
                        length = response.headers.get("Content-Length")
                        expected = int(length) if length is not None else None
                        remaining = min(
                            self.max_bytes - self.network_bytes,
                            self.max_bytes - self.source_bytes,
                        )
                        if expected is not None and not 0 <= expected <= remaining:
                            raise ValueError(
                                "Context asset exceeds remaining byte budget"
                            )
                        blocks, size = [], 0
                        while expected is None or size < expected:
                            remaining = min(
                                self.max_bytes - self.network_bytes,
                                self.max_bytes - self.source_bytes - size,
                            )
                            if remaining <= 0:
                                raise ValueError(
                                    "Context byte budget exhausted before confirmed EOF"
                                )
                            block = response.read(
                                min(
                                    65536,
                                    remaining,
                                    expected - size
                                    if expected is not None
                                    else remaining,
                                )
                            )
                            self.network_bytes += len(block)
                            size += len(block)
                            if not block:
                                if expected is not None and size != expected:
                                    raise URLError("Truncated context response")
                                break
                            blocks.append(block)
                        blob = b"".join(blocks)
                    partial = raw_path.with_suffix(".part")
                    partial.write_bytes(blob)
                    partial.replace(raw_path)
                    write_json(
                        meta_path,
                        {"url": url, "bytes": len(blob), "sha256": digest(blob)},
                    )
                    break
                except HTTPError as exc:
                    if attempt or (exc.code != 429 and exc.code < 500):
                        raise
                except (URLError, TimeoutError):
                    if attempt:
                        raise
            else:
                raise RuntimeError("Context download did not complete")
        if key not in self.seen:
            self.source_bytes += len(blob)
            self.seen.add(key)
        return blob


def select_items(snapshot, ward_code, types, max_lod=3):
    if snapshot.get("schema_version") != 1:
        raise ValueError("Unsupported source inventory")
    if (
        not types
        or not set(types) <= MODEL_TYPES
        or type(max_lod) is not int
        or not 1 <= max_lod <= 4
    ):
        raise ValueError(
            "Select vegetation, transportation or water and an explicit LOD limit"
        )
    matches = [ward for ward in snapshot["wards"] if ward["code"] == ward_code]
    if len(matches) != 1 or matches[0]["metadata_status"] != "confirmed":
        raise ValueError("Ward is not confirmed in the source inventory")
    ward = matches[0]
    if not ward.get("package") or not ward["package"].get("license", {}).get("url"):
        raise ValueError("Context source requires recorded package terms")
    groups = {}
    for item in ward["catalog"]["items"]:
        if item["type"] not in types or item["format"] != "CESIUM3DTILES":
            continue
        if type(item["lod"]) is not int or not 0 <= item["lod"] <= max_lod:
            continue
        validate_url(item["url"])
        if not urlparse(item["url"]).path.endswith("/tileset.json"):
            raise ValueError("Context source is not a direct tileset metadata URL")
        group = groups.setdefault(item["dataset_id"], [])
        group.append(item)
    selected = [
        max(
            items,
            key=lambda item: (item["lod"], item["texture"] == "TEXTURE", item["id"]),
        )
        for items in groups.values()
    ]
    if set(types) - {item["type"] for item in selected}:
        raise ValueError("Requested 3D context type is not listed at the allowed LOD")
    return ward, sorted(selected, key=lambda item: (item["type"], item["id"]))


def validate_tileset(data):
    """Conservative adapter preflight, before any geometry payload is fetched."""
    if not isinstance(data, dict) or not isinstance(data.get("root"), dict):
        raise ValueError("Expected a 3D Tiles root")
    pending, count = [data["root"]], 0
    while pending:
        node = pending.pop()
        count += 1
        if count > 100_000:
            raise ValueError("Context tileset node budget exceeded")
        if not isinstance(node, dict):
            raise ValueError("Context tiles must be objects")
        if "implicitTiling" in node or "contents" in node:
            raise ValueError(
                "Implicit tiling and multiple contents need a separate adapter"
            )
        validate_region(node.get("boundingVolume"))
        content = node.get("content")
        if content is not None:
            if not isinstance(content, dict):
                raise ValueError("Context tile content must be an object")
            if "boundingVolume" in content:
                validate_region(content["boundingVolume"])
        if "transform" in node and (
            not isinstance(node["transform"], list)
            or len(node["transform"]) != 16
            or any(
                type(value) not in (int, float) or not math.isfinite(value)
                for value in node["transform"]
            )
        ):
            raise ValueError("Context tile transform must have 16 finite numbers")
        children = node.get("children", [])
        if not isinstance(children, list):
            raise ValueError("Context tile children must be an array")
        pending.extend(children)


def validate_region(bounds):
    region = bounds.get("region") if isinstance(bounds, dict) else None
    if (
        not isinstance(region, list)
        or len(region) != 6
        or any(
            type(value) not in (int, float) or not math.isfinite(value)
            for value in region
        )
    ):
        raise ValueError("Context sampler requires six finite region bounds")
    if not (
        -math.pi <= region[0] <= region[2] <= math.pi
        and -math.pi / 2 <= region[1] <= region[3] <= math.pi / 2
        and region[4] <= region[5]
    ):
        raise ValueError("Context region is invalid or crosses the antimeridian")


def dependency_path(uri):
    parsed = urlparse(uri)
    decoded = unquote(parsed.path)
    path = PurePosixPath(decoded)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or not decoded
        or "\\" in decoded
        or "\x00" in decoded
        or path.is_absolute()
        or ".." in path.parts
    ):
        raise ValueError("Unsupported or escaping external GLB dependency")
    return path


def preflight(items, bbox, cache):
    plans = []
    for item in items:
        raw = cache.fetch(item["url"])
        if len(raw) > 2_000_000:
            raise ValueError("Context tileset metadata exceeds two million bytes")
        data = json.loads(raw)
        validate_tileset(data)
        selected = select_leaf_tiles(data, bbox)
        if len(selected) > 64:
            raise ValueError("Context source selects more than 64 tiles")
        for tile in selected:
            validate_url(urljoin(item["url"], tile["uri"]))
        plans.append({"item": item, "metadata": raw, "tiles": selected})
    return plans


def context_tile(tile, item, cache, staging):
    url = validate_url(urljoin(item["url"], tile["uri"]))
    blob = cache.fetch(url)
    key = digest(url.encode())[:24]
    extension = Path(urlparse(url).path).suffix.lower()
    if extension == ".b3dm":
        glb, feature, batch = unpack_b3dm(blob)
        lod_counts = dict(
            Counter(str(value) for value in batch_scalar_values(blob, "_lod"))
        )
    elif extension == ".glb":
        glb, feature, batch, lod_counts = blob, {}, {}, {}
    else:
        raise ValueError("Context payload requires b3dm or GLB")
    count = feature.get("BATCH_LENGTH")
    if count is not None and (type(count) is not int or count < 0):
        raise ValueError("Context BATCH_LENGTH must be a nonnegative integer")
    metadata = glb_json(glb)
    raw_path, glb_path = (
        Path("raw") / (key + extension),
        Path("glb") / key / "model.glb",
    )
    for path, value in ((raw_path, blob), (glb_path, glb)):
        (staging / path).parent.mkdir(parents=True, exist_ok=True)
        (staging / path).write_bytes(value)
    dependencies = []
    for resource in metadata.get("buffers", []) + metadata.get("images", []):
        uri = resource.get("uri")
        if not uri or uri.startswith("data:"):
            continue
        relative = dependency_path(uri)
        dependency_url = validate_url(urljoin(url, uri))
        dependency_blob = cache.fetch(dependency_url)
        destination = glb_path.parent / relative
        if destination == glb_path or (
            (staging / destination).exists()
            and (staging / destination).read_bytes() != dependency_blob
        ):
            raise ValueError("External dependency collides with an existing GLB asset")
        (staging / destination).parent.mkdir(parents=True, exist_ok=True)
        (staging / destination).write_bytes(dependency_blob)
        dependencies.append(
            {
                "path": destination.as_posix(),
                "url": dependency_url,
                "sha256": digest(dependency_blob),
                "bytes": len(dependency_blob),
            }
        )
    feature_rtc = feature.get("RTC_CENTER")
    gltf_rtc = metadata.get("extensions", {}).get("CESIUM_RTC", {}).get("center")
    if feature_rtc is not None and gltf_rtc is not None:
        raise ValueError("Resolve simultaneous feature and GLB RTC centers explicitly")
    rtc = (
        feature_rtc
        if feature_rtc is not None
        else gltf_rtc
        if gltf_rtc is not None
        else [0, 0, 0]
    )
    if (
        not isinstance(rtc, list)
        or len(rtc) != 3
        or any(
            type(value) not in (int, float) or not math.isfinite(value) for value in rtc
        )
    ):
        raise ValueError("Invalid context RTC center")
    table_path = Path("metadata") / (key + "-tables.json")
    write_json(staging / table_path, {"feature_table": feature, "batch_table": batch})
    identifiers = {
        name: values
        for name, values in batch.items()
        if name in {"gml_id", "gml:id", "id"} and isinstance(values, list)
    }
    return {
        **tile,
        "source_item_id": item["id"],
        "context_type": item["type"],
        "url": url,
        "path": raw_path.as_posix(),
        "glb_path": glb_path.as_posix(),
        "sha256": digest(blob),
        "glb_sha256": digest(glb),
        "bytes": len(blob),
        "glb_bytes": len(glb),
        "rtc_center": rtc,
        "feature_count": count,
        "feature_ids": identifiers,
        "lod_counts": lod_counts,
        "tables_path": table_path.as_posix(),
        "tables_sha256": digest((staging / table_path).read_bytes()),
        "dependencies": dependencies,
        "gltf_extensions_used": metadata.get("extensionsUsed", []),
        "gltf_extensions_required": metadata.get("extensionsRequired", []),
    }


def download_context(
    snapshot_path,
    config_path,
    output,
    cache_path,
    *,
    ward_code="13104",
    types=("veg",),
    max_lod=3,
    max_bytes=MAX_BUDGET_BYTES,
    preflight_only=False,
    cache=None,
):
    snapshot_raw = Path(snapshot_path).read_bytes()
    snapshot = json.loads(snapshot_raw)
    config = load_config(config_path)
    ward, items = select_items(snapshot, ward_code, types, max_lod)
    cache = cache if cache is not None else BoundedCache(cache_path, max_bytes)
    plans = preflight(items, list(config.source_bounds_wsen), cache)
    report = {
        "selected_items": [
            {
                "id": plan["item"]["id"],
                "type": plan["item"]["type"],
                "lod": plan["item"]["lod"],
                "intersecting_tiles": len(plan["tiles"]),
            }
            for plan in plans
        ],
        "network_bytes": cache.network_bytes,
        "source_bytes": cache.source_bytes,
    }
    if preflight_only:
        return {"status": "metadata-preflight-only", **report}
    if not any(plan["tiles"] for plan in plans):
        raise ValueError(
            "No selected context geometry intersects the requested source bounds"
        )
    output = Path(output)
    if output.is_symlink() or (
        output.exists() and (not output.is_dir() or not (output / MARKER).is_file())
    ):
        raise ValueError("Refusing to replace an unknown context directory")
    backup = output.with_name(f".{output.name}-previous")
    if backup.exists():
        raise ValueError("Previous context backup exists; inspect before replacing")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".context-stage-", dir=output.parent))
    try:
        (staging / MARKER).write_text("PLATEAU bounded context sample v1\n")
        sources, tiles = [], []
        for plan in plans:
            item = plan["item"]
            metadata_path = Path("metadata") / (
                digest(item["url"].encode())[:24] + "-tileset.json"
            )
            (staging / metadata_path).parent.mkdir(parents=True, exist_ok=True)
            (staging / metadata_path).write_bytes(plan["metadata"])
            sources.append(
                {
                    **item,
                    "metadata_path": metadata_path.as_posix(),
                    "metadata_sha256": digest(plan["metadata"]),
                    "intersecting_tile_count": len(plan["tiles"]),
                }
            )
            for tile in plan["tiles"]:
                tiles.append(context_tile(tile, item, cache, staging))
        manifest = {
            "schema_version": 1,
            "kind": "plateau-context",
            "status": "complete",
            "ward_code": ward_code,
            "edition": ward["edition"],
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "inventory_sha256": digest(snapshot_raw),
            "config_sha256": config.fingerprint,
            "package": ward["package"],
            "sources": sources,
            "attribution": f"Project PLATEAU, {ward['name_en']} {ward['edition']} catalog sources. Selected context tiles, original geometry retained.",
            "license": ward["package"]["license"],
            "bbox": list(config.source_bounds_wsen),
            "origin": {
                "lon": config.origin_lonlat[0],
                "lat": config.origin_lonlat[1],
                "height": 0,
            },
            "selection": "Highest selected LOD per catalog dataset, terminal intersecting region tiles; entire original tiles retained.",
            "coordinate_convention": "Accumulated column-major tile transforms and RTC centers retained; original GLB node transforms remain embedded.",
            "source_coverage_verified": False,
            "acquisition_dates_verified": False,
            "download_bytes": cache.network_bytes,
            "source_bytes": cache.source_bytes,
            "byte_budget": max_bytes,
            "tile_count": len(tiles),
            "feature_count": (
                sum(tile["feature_count"] for tile in tiles)
                if all(tile["feature_count"] is not None for tile in tiles)
                else None
            ),
            "tiles": tiles,
        }
        write_json(staging / "manifest.json", manifest)
        if output.exists():
            output.rename(backup)
        try:
            staging.rename(output)
        except BaseException:
            if backup.exists():
                backup.rename(output)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("configs/sources-tokyo-2025.snapshot.json"),
    )
    parser.add_argument("--config", type=Path, default=Path("configs/shinjuku.json"))
    parser.add_argument("--output", type=Path, default=Path("data/context"))
    parser.add_argument("--cache", type=Path, default=Path(".cache/context-downloads"))
    parser.add_argument(
        "--types", nargs="+", choices=sorted(MODEL_TYPES), default=["veg"]
    )
    parser.add_argument("--max-lod", type=int, default=3)
    parser.add_argument("--max-bytes", type=int, default=MAX_BUDGET_BYTES)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    result = download_context(
        args.inventory,
        args.config,
        args.output,
        args.cache,
        types=tuple(args.types),
        max_lod=args.max_lod,
        max_bytes=args.max_bytes,
        preflight_only=args.preflight_only,
    )
    if result["status"] == "complete":
        result = {
            key: result[key]
            for key in (
                "status",
                "tile_count",
                "feature_count",
                "download_bytes",
                "source_bytes",
                "byte_budget",
            )
        }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

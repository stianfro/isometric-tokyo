"""Inventory Tokyo ward source metadata without downloading model archives.

Official portal links identify CKAN packages. A filtered PLATEAU GraphQL query
provides dataset/item identities and model URLs. Catalog claims are not spatial
coverage, imagery-capture dates, texture completeness or renderer validation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

PORTAL = "https://front.geospatial.jp/plateau_portal_site/"
CKAN = "https://www.geospatial.jp/ckan/api/3/action/package_show"
GRAPHQL = "https://api.plateauview.mlit.go.jp/datacatalog/graphql"
ALLOWED_HOSTS = {
    "front.geospatial.jp",
    "www.geospatial.jp",
    "api.plateauview.mlit.go.jp",
}
MODEL_TYPES = (
    "bldg",
    "tran",
    "veg",
    "wtr",
    "brid",
    "dem",
    "railway",
    "park",
    "station",
    "border",
)
MAX_RESPONSE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 20_000_000
MAX_REQUESTS = 52


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def utc_now():
    return datetime.now(UTC).isoformat(timespec="seconds")


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, encoding="utf-8", delete=False
    ) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class MetadataCache:
    def __init__(
        self,
        directory,
        *,
        offline=False,
        refresh=False,
        opener=urlopen,
        sleep=time.sleep,
    ):
        if offline and refresh:
            raise ValueError("Offline and refresh modes cannot be combined")
        self.directory = Path(directory)
        self.offline, self.refresh = offline, refresh
        self.opener, self.sleep = opener, sleep
        self.requests, self.bytes = 0, 0
        self.evidence = []

    def fetch(self, url, body=None):
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in ALLOWED_HOSTS
            or (url not in {PORTAL, GRAPHQL} and url.split("?", 1)[0] != CKAN)
        ):
            raise ValueError("Only official metadata endpoints can be fetched")
        request_key = sha256(canonical({"url": url, "body": body}).encode())
        path = self.directory / f"{request_key}.json"
        if path.exists() and not self.refresh:
            cached = json.loads(path.read_text())
            if (
                cached["url"] != url
                or cached["request_sha256"] != request_key
                or cached["sha256"] != sha256(cached["body"].encode())
                or len(cached["body"].encode()) > MAX_RESPONSE_BYTES
            ):
                raise ValueError("Metadata cache checksum mismatch")
            self.evidence.append(
                {
                    key: cached[key]
                    for key in ("url", "request_sha256", "sha256", "fetched_at")
                }
            )
            return cached["body"]
        if self.offline:
            raise ValueError("Requested metadata is absent from the offline cache")
        request = Request(
            url,
            data=canonical(body).encode() if body is not None else None,
            headers={
                "User-Agent": "isometric-tokyo-source-inventory/1",
                "Accept": "application/json, text/html",
                "Content-Type": "application/json",
            },
        )
        for attempt in range(2):
            if self.requests >= MAX_REQUESTS:
                raise ValueError("Metadata request limit reached")
            self.requests += 1
            try:
                with self.opener(request, timeout=30) as response:
                    length = response.headers.get("Content-Length")
                    if length and int(length) > MAX_RESPONSE_BYTES:
                        raise ValueError("Metadata response exceeds size limit")
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                self.bytes += len(raw)
                if len(raw) > MAX_RESPONSE_BYTES or self.bytes > MAX_TOTAL_BYTES:
                    raise ValueError("Metadata download exceeds byte limit")
                text = raw.decode("utf-8")
                cached = {
                    "url": url,
                    "request_sha256": request_key,
                    "sha256": sha256(raw),
                    "fetched_at": utc_now(),
                    "body": text,
                }
                atomic_json(path, cached)
                self.evidence.append(
                    {
                        key: cached[key]
                        for key in ("url", "request_sha256", "sha256", "fetched_at")
                    }
                )
                return text
            except HTTPError as exc:
                if attempt or (exc.code != 429 and exc.code < 500):
                    raise
            except (URLError, TimeoutError):
                if attempt:
                    raise
            self.sleep(1)
        raise RuntimeError("Unreachable metadata retry state")


def load_config(path):
    config = json.loads(Path(path).read_text())
    if type(config.get("schema_version")) is not int or config["schema_version"] != 1:
        raise ValueError("Unsupported source inventory configuration")
    year = config["edition"]
    if type(year) is not int or not 2020 <= year <= 2100:
        raise ValueError("Expected a supported numeric dataset edition")
    wards = config["wards"]
    if not isinstance(wards, list) or len(wards) != 23:
        raise ValueError("The Tokyo target must contain exactly 23 special wards")
    codes = [ward["code"] for ward in wards]
    if set(codes) != {str(code) for code in range(13101, 13124)}:
        raise ValueError("Ward codes must be unique Tokyo special ward codes")
    for ward in wards:
        if not all(
            isinstance(ward[key], str) and ward[key].strip()
            for key in ("name_ja", "name_en")
        ):
            raise ValueError("Each ward needs Japanese and English names")
    return config


class DatasetLinks(HTMLParser):
    def __init__(self, edition):
        super().__init__()
        self.edition, self.links = edition, {}

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        url = dict(attrs).get("href", "")
        parsed = urlparse(url)
        match = re.fullmatch(
            r"/ckan/dataset/(plateau-(131\d{2})-[a-z-]+-(\d{4}))/?", parsed.path
        )
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"www.geospatial.jp", "geospatial.jp"}
            or not match
            or int(match[3]) != self.edition
        ):
            return
        code, package = match[2], match[1]
        if code in self.links and self.links[code] != package:
            raise ValueError("Portal lists ambiguous packages for a ward")
        self.links[code] = package


def catalog_query(config):
    codes = json.dumps([ward["code"] for ward in config["wards"]])
    year, types = config["edition"], json.dumps(MODEL_TYPES)
    return (
        """{
      datasets(input: {areaCodes: CODES, year: YEAR, includeTypes: TYPES}) {
        id name year cityCode typeCode
        items { id name url format
          ... on PlateauDatasetItem { lod lodEx texture formatVersion compositeUrl }
        }
      }
      citygmlDatasets(input: {cityCodes: CODES, years: [YEAR]}) {
        id year cityCode featureTypes gspatialjpDatasetUrl metadataZipUrls
        plateauSpecMinor { version }
      }
    }""".replace("CODES", codes)
        .replace("YEAR", str(year))
        .replace("TYPES", types)
    )


def public_url(value):
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return None
    return value


def package_summary(response, package):
    if response.get("success") is not True or not isinstance(
        response.get("result"), dict
    ):
        raise ValueError("CKAN did not return a successful package")
    result = response["result"]
    if (
        result.get("name") != package
        or result.get("state") != "active"
        or result.get("private") is not False
    ):
        raise ValueError("CKAN package identity or public status disagrees")
    resources = []
    for resource in result.get("resources", []):
        url = public_url(resource.get("url"))
        if not url:
            continue
        size = resource.get("size")
        resources.append(
            {
                "id": resource["id"],
                "name": resource.get("name"),
                "format": resource.get("format"),
                "url": url,
                "size_bytes": size if type(size) is int and size >= 0 else None,
            }
        )
    return {
        "id": result["id"],
        "name": package,
        "title": result.get("title"),
        "url": f"https://www.geospatial.jp/ckan/dataset/{package}",
        "metadata_modified": result.get("metadata_modified"),
        "license": {
            "id": result.get("license_id"),
            "title": result.get("license_title"),
            "url": public_url(result.get("license_url")),
        },
        "resources": resources,
    }


def catalog_by_ward(response, config):
    if response.get("errors") or not isinstance(response.get("data"), dict):
        raise ValueError(
            "PLATEAU catalog query failed; partial responses are not inventory"
        )
    codes = {ward["code"] for ward in config["wards"]}
    result = {code: {"items": [], "citygml": []} for code in codes}
    dataset_ids, item_ids = set(), set()
    for dataset in response["data"]["datasets"]:
        code = dataset["cityCode"]
        if (
            code not in codes
            or dataset["year"] != config["edition"]
            or dataset["typeCode"] not in MODEL_TYPES
            or dataset["id"] in dataset_ids
        ):
            raise ValueError("Catalog dataset falls outside the requested inventory")
        dataset_ids.add(dataset["id"])
        for item in dataset["items"]:
            if item["id"] in item_ids or not public_url(item["url"]):
                raise ValueError("Catalog item identity or URL is invalid")
            item_ids.add(item["id"])
            result[code]["items"].append(
                {
                    "id": item["id"],
                    "dataset_id": dataset["id"],
                    "type": dataset["typeCode"],
                    "name": item["name"],
                    "format": item["format"],
                    "lod": item.get("lod"),
                    "lod_extension": item.get("lodEx"),
                    "texture": item.get("texture"),
                    "format_version": item.get("formatVersion"),
                    "url": item["url"],
                    "composite_url": public_url(item.get("compositeUrl")),
                }
            )
    for dataset in response["data"]["citygmlDatasets"]:
        code = dataset["cityCode"]
        if code not in codes or dataset["year"] != config["edition"]:
            raise ValueError("CityGML dataset falls outside the requested inventory")
        result[code]["citygml"].append(
            {
                "id": dataset["id"],
                "specification_version": dataset["plateauSpecMinor"]["version"],
                "feature_types": sorted(dataset["featureTypes"]),
                "ckan_url": public_url(dataset.get("gspatialjpDatasetUrl")),
                "metadata_zip_urls": [
                    url for url in dataset["metadataZipUrls"] if public_url(url)
                ],
            }
        )
    for ward in result.values():
        ward["items"].sort(key=lambda item: (item["type"], item["id"]))
        ward["citygml"].sort(key=lambda item: item["id"])
    return result


def readiness(catalog):
    items = catalog["items"]
    buildings = [
        item
        for item in items
        if item["type"] == "bldg" and item["format"] == "CESIUM3DTILES"
    ]
    return {
        "building_tilesets_listed": bool(buildings),
        "building_lods_listed": sorted(
            {item["lod"] for item in buildings if type(item["lod"]) is int}
        ),
        "textured_building_tileset_listed": any(
            item["texture"] == "TEXTURE" for item in buildings
        ),
        "geometry_layers_listed": sorted(
            {item["type"] for item in items if item["format"] == "CESIUM3DTILES"}
        ),
        "reference_layers_listed": sorted(
            {
                item["type"]
                for item in items
                if item["format"] in {"GEOJSON", "CZML", "MVT"}
            }
        ),
        "terrain_in_citygml_catalog": any(
            "dem" in dataset["feature_types"] for dataset in catalog["citygml"]
        ),
        "acquisition_dates": None,
        "spatial_coverage_verified": False,
        "per_building_texture_completeness": None,
        "source_format_adapter_verified": False,
        "ready_for_district_production": False,
    }


def inventory(config, cache):
    parser = DatasetLinks(config["edition"])
    parser.feed(cache.fetch(PORTAL))
    catalog = catalog_by_ward(
        json.loads(cache.fetch(GRAPHQL, {"query": catalog_query(config)})), config
    )
    wards = []
    for ward in sorted(config["wards"], key=lambda value: value["code"]):
        code = ward["code"]
        package = parser.links.get(code)
        package_data = (
            package_summary(
                json.loads(cache.fetch(CKAN + "?" + urlencode({"id": package}))),
                package,
            )
            if package
            else None
        )
        if package_data and any(
            dataset["ckan_url"]
            and dataset["ckan_url"].rstrip("/") != package_data["url"]
            for dataset in catalog[code]["citygml"]
        ):
            raise ValueError(
                "Portal, CKAN and CityGML catalog package identities disagree"
            )
        wards.append(
            ward
            | {
                "edition": config["edition"],
                "metadata_status": "confirmed"
                if package_data
                else "not_listed_in_portal",
                "package": package_data,
                "catalog": catalog[code],
                "readiness": readiness(catalog[code]),
            }
        )
    return {
        "schema_version": 1,
        "edition": config["edition"],
        "generated_at": utc_now(),
        "scope": "Tokyo 23 special wards, metadata inventory only",
        "evidence": cache.evidence,
        "summary": {
            "ward_count": len(wards),
            "packages_confirmed": sum(ward["package"] is not None for ward in wards),
            "wards_with_building_tilesets": sum(
                ward["readiness"]["building_tilesets_listed"] for ward in wards
            ),
            "wards_with_textured_building_tilesets": sum(
                ward["readiness"]["textured_building_tileset_listed"] for ward in wards
            ),
            "production_ready_wards": 0,
        },
        "unknowns": [
            "Acquisition dates require source metadata, not edition or modification dates.",
            "Listed LOD and textures do not establish whole-ward completeness.",
            "Tileset structure, geometry, textures and source-boundary joins need bounded spatial probes.",
            "Orthophoto and terrain service coverage is not established by this catalog snapshot.",
            "Related railway information is not a railway geometry model.",
            "No full model, archive, imagery or terrain payload was downloaded.",
        ],
        "wards": wards,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path("configs/sources-tokyo-wards.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("configs/sources-tokyo-2025.snapshot.json")
    )
    parser.add_argument("--cache", type=Path, default=Path(".cache/source-inventory"))
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    cache = MetadataCache(args.cache, offline=args.offline, refresh=args.refresh)
    result = inventory(config, cache)
    atomic_json(args.output, result)
    print(
        json.dumps(
            result["summary"]
            | {
                "network_requests": cache.requests,
                "downloaded_metadata_bytes": cache.bytes,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

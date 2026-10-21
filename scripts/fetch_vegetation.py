"""Bounded OSM vegetation with explicit mapped-source and artistic-crown provenance.

This is a separate ODbL data adapter, not a PLATEAU survey or a tree inventory.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import shutil
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from fetch_context import NoRedirect
from geo import local_point, local_to_geodetic

ENDPOINT = "https://overpass-api.de/api/interpreter"
LICENSE = "https://opendatacommons.org/licenses/odbl/1-0/"
ATTRIBUTION = "© OpenStreetMap contributors"
MAX_BYTES = 25_000_000
MAX_FEATURES = 30_000
MAX_COORDINATES = 300_000
MARKER = ".osm-vegetation"


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def json_bytes(value):
    return (
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode()


def validate_bbox(bbox):
    if (
        not isinstance(bbox, (list, tuple))
        or len(bbox) != 4
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in bbox)
    ):
        raise ValueError("Expected four finite geographic bounds")
    w, s, e, n = bbox
    if not (-180 <= w < e <= 180 and -85 <= s < n <= 85):
        raise ValueError("Expected non-wrapping geographic bounds")
    if e - w > 0.08 or n - s > 0.08 or (e - w) * (n - s) > 0.004:
        raise ValueError("Vegetation request exceeds the bounded study area")
    return list(bbox)


def query_for(bbox):
    w, s, e, n = validate_bbox(bbox)
    area = f"({s:.8f},{w:.8f},{n:.8f},{e:.8f})"
    filters = [
        '["natural"~"^(wood|scrub|grassland|water)$"]',
        '["landuse"~"^(forest|grass)$"]',
        '["landcover"="trees"]',
        '["leisure"~"^(park|garden|pitch|playground)$"]',
        '["building"]',
    ]
    selectors = [f'node["natural"="tree"]{area};', f'way["highway"]{area};']
    selectors.extend(
        f"{kind}{tags}{area};" for tags in filters for kind in ("way", "relation")
    )
    # Server memory and client transfer budgets are independent.
    return (
        "[out:json][timeout:45][maxsize:25000000];("
        + "".join(selectors)
        + ");out meta geom;"
    )


def fetch_response(query, cache, *, max_bytes=5_000_000, offline=False, opener=None):
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BYTES:
        raise ValueError("Vegetation transfer budget must be in [1,25000000]")
    cache = Path(cache)
    key = digest((ENDPOINT + "\n" + query).encode())
    raw, record = cache / f"{key}.json", cache / f"{key}.meta.json"
    if raw.exists() and record.exists():
        metadata = json.loads(record.read_text())
        if not isinstance(metadata, dict):
            raise ValueError("Invalid vegetation cache record")
        if raw.stat().st_size > max_bytes:
            raise ValueError("Cached vegetation response exceeds byte budget")
        blob = raw.read_bytes()
        if (
            metadata.get("endpoint") != ENDPOINT
            or metadata.get("query_sha256") != digest(query.encode())
            or metadata.get("sha256") != digest(blob)
            or metadata.get("bytes") != len(blob)
        ):
            raise ValueError("Vegetation cache checksum or provenance mismatch")
        return blob, metadata, 0
    if offline:
        raise ValueError("No verified cached response for this query")
    open_request = opener if opener is not None else build_opener(NoRedirect()).open
    request = Request(
        ENDPOINT,
        data=urlencode({"data": query}).encode(),
        headers={
            "User-Agent": "isometric-tokyo-vegetation/1",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with open_request(request, timeout=60) as response:
        length = response.headers.get("Content-Length")
        if length is not None and not 0 <= int(length) <= max_bytes:
            raise ValueError("Vegetation response exceeds byte budget")
        blob = response.read(max_bytes + 1)
        if len(blob) > max_bytes:
            raise ValueError("Vegetation response exceeds byte budget")
        if length is not None and len(blob) != int(length):
            raise ValueError("Truncated vegetation response")
    payload = json.loads(blob)
    if payload.get("remark") or not isinstance(payload.get("elements"), list):
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"{key}.failure.json").write_text(
            json.dumps(
                {
                    "endpoint": ENDPOINT,
                    "query_sha256": digest(query.encode()),
                    "bytes": len(blob),
                    "sha256": digest(blob),
                    "remark": payload.get("remark"),
                    "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                },
                indent=2,
            )
            + "\n"
        )
        raise ValueError(
            f"Overpass returned incomplete results or an error ({len(blob)} response bytes): {payload.get('remark', 'missing elements')}"
        )
    metadata = {
        "endpoint": ENDPOINT,
        "query_sha256": digest(query.encode()),
        "sha256": digest(blob),
        "bytes": len(blob),
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    cache.mkdir(parents=True, exist_ok=True)
    raw.with_suffix(".part").write_bytes(blob)
    raw.with_suffix(".part").replace(raw)
    record.write_bytes(json_bytes(metadata))
    return blob, metadata, len(blob)


def coordinate(raw):
    if not isinstance(raw, dict):
        raise ValueError("Missing source coordinate")
    lon, lat = raw.get("lon"), raw.get("lat")
    if any(
        type(v) not in (int, float) or not math.isfinite(v) for v in (lon, lat)
    ) or not (-180 <= lon <= 180 and -85 <= lat <= 85):
        raise ValueError("Invalid source coordinate")
    return [lon, lat]


def line_coordinates(raw):
    if not isinstance(raw, list) or not 2 <= len(raw) <= 20_000:
        raise ValueError("Unsupported source line size")
    return [coordinate(point) for point in raw]


def point_in_ring(point, ring):
    x, y = point[:2]
    inside = False
    for a, b in zip(ring, ring[1:], strict=False):
        if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (
            b[1] - a[1]
        ) + a[0]:
            inside = not inside
    return inside


def join_rings(parts):
    pending = [list(part) for part in parts]
    result = []
    while pending:
        ring = pending.pop(0)
        while ring[0] != ring[-1]:
            candidates = [
                (index, part)
                for index, part in enumerate(pending)
                if part[0] == ring[-1] or part[-1] == ring[-1]
            ]
            if len(candidates) != 1:
                raise ValueError("Unclosed or ambiguous multipolygon ring")
            index, part = candidates[0]
            pending.pop(index)
            ring.extend((part if part[0] == ring[-1] else part[::-1])[1:])
        if len(ring) < 4 or len({tuple(point) for point in ring}) < 3:
            raise ValueError("Degenerate source polygon")
        result.append(ring)
    return result


def geometry_for(element, kind):
    if element["type"] == "node":
        return {"type": "Point", "coordinates": coordinate(element)}
    if element["type"] == "way":
        coordinates = line_coordinates(element.get("geometry"))
        if kind == "exclusion_road" and element.get("tags", {}).get("area") != "yes":
            return {"type": "LineString", "coordinates": coordinates}
        if coordinates[0] != coordinates[-1]:
            raise ValueError("Open way cannot be interpreted as an area")
        return {"type": "Polygon", "coordinates": join_rings([coordinates])}
    if (
        element["type"] == "relation"
        and kind == "exclusion_building"
        and element.get("tags", {}).get("type") in {"building", "site"}
    ):
        bounds = element.get("bounds", {})
        west, south = coordinate(
            {"lon": bounds.get("minlon"), "lat": bounds.get("minlat")}
        )
        east, north = coordinate(
            {"lon": bounds.get("maxlon"), "lat": bounds.get("maxlat")}
        )
        if west >= east or south >= north:
            raise ValueError("Empty building relation bounds")
        return {
            "type": "Polygon",
            "coordinates": [
                [
                    [west, south],
                    [east, south],
                    [east, north],
                    [west, north],
                    [west, south],
                ]
            ],
        }
    if (
        element["type"] != "relation"
        or element.get("tags", {}).get("type") != "multipolygon"
    ):
        raise ValueError("Unsupported source geometry")
    groups = {"outer": [], "inner": []}
    for member in element.get("members", []):
        role = member.get("role") or "outer"
        if member.get("type") != "way" or role not in groups:
            raise ValueError("Unsupported multipolygon member")
        groups[role].append(line_coordinates(member.get("geometry")))
    polygons = [[ring] for ring in join_rings(groups["outer"])]
    if not polygons:
        raise ValueError("Multipolygon contains no exterior")
    for hole in join_rings(groups["inner"]):
        owners = [polygon for polygon in polygons if point_in_ring(hole[0], polygon[0])]
        if len(owners) != 1:
            raise ValueError("Multipolygon hole has no unambiguous exterior")
        owners[0].append(hole)
    return {"type": "MultiPolygon", "coordinates": polygons}


def classify(tags):
    # Conflicting feature tags are resolved conservatively toward exclusions.
    if tags.get("building") not in (None, "no"):
        return "exclusion_building"
    if "highway" in tags:
        return "exclusion_road"
    if tags.get("natural") == "water":
        return "exclusion_water"
    if tags.get("leisure") in ("pitch", "playground"):
        return "exclusion_play_area"
    if tags.get("natural") == "tree":
        return "mapped_tree"
    if tags.get("barrier") == "hedge" or tags.get("natural") == "scrub":
        return "mapped_scrub"
    if (
        tags.get("natural") == "wood"
        or tags.get("landuse") == "forest"
        or tags.get("landcover") == "trees"
    ):
        return "mapped_woodland"
    if tags.get("natural") == "grassland" or tags.get("landuse") == "grass":
        return "mapped_grass"
    if tags.get("leisure") in ("park", "garden"):
        return "park_boundary"
    return None


def normalize(payload, *, max_features=MAX_FEATURES, max_coordinates=MAX_COORDINATES):
    if (
        type(max_features) is not int
        or type(max_coordinates) is not int
        or not 1 <= max_features <= 90000
        or not 1 <= max_coordinates <= 900000
    ):
        raise ValueError("Vegetation normalization exceeds the finite expansion budget")
    if (
        not isinstance(payload, dict)
        or payload.get("remark")
        or not isinstance(payload.get("elements"), list)
        or len(payload["elements"]) > max_features
    ):
        raise ValueError("Incomplete or oversized Overpass result")
    features, skipped, identifiers = [], [], set()
    for element in payload["elements"]:
        if (
            not isinstance(element, dict)
            or type(element.get("id")) is not int
            or element.get("type") not in ("node", "way", "relation")
        ):
            raise ValueError("Invalid OSM source identity")
        source_id = f"osm:{element['type']}/{element['id']}"
        if source_id in identifiers:
            raise ValueError("Duplicate OSM source identity")
        identifiers.add(source_id)
        tags = element.get("tags", {})
        if not isinstance(tags, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in tags.items()
        ):
            raise ValueError("Invalid OSM source tags")
        kind = classify(tags)
        if not kind:
            continue
        try:
            geometry = geometry_for(element, kind)
        except ValueError as error:
            if kind.startswith("exclusion_"):
                raise ValueError(
                    f"Cannot safely interpret exclusion {source_id}"
                ) from error
            skipped.append({"source_id": source_id, "reason": str(error)})
            continue
        features.append(
            {
                "type": "Feature",
                "id": source_id,
                "geometry": geometry,
                "properties": {
                    "source_id": source_id,
                    "source_kind": kind,
                    "source_url": f"https://www.openstreetmap.org/{element['type']}/{element['id']}",
                    "source_version": element.get("version"),
                    "source_timestamp": element.get("timestamp"),
                    "geometry_interpretation": "conservative_building_relation_bounds"
                    if element["type"] == "relation"
                    and tags.get("type") in {"building", "site"}
                    and kind == "exclusion_building"
                    else "mapped_geometry",
                    "tags": tags,
                    "canopy_representation": "unknown",
                    "survey_verified": False,
                },
            }
        )

    # A conservative total-coordinate bound also covers nested relations.
    def count(value):
        if isinstance(value, list):
            return (
                1
                if value and isinstance(value[0], (int, float))
                else sum(count(item) for item in value)
            )
        return 0

    if (
        sum(count(feature["geometry"]["coordinates"]) for feature in features)
        > max_coordinates
    ):
        raise ValueError("Vegetation geometry coordinate budget exceeded")
    return sorted(features, key=lambda feature: feature["id"]), skipped


def polygons(geometry):
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiPolygon":
        return geometry["coordinates"]
    return []


def segment_distance(point, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == dy == 0:
        return math.hypot(point[0] - a[0], point[1] - a[1])
    t = max(
        0,
        min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / (dx * dx + dy * dy)),
    )
    return math.hypot(point[0] - a[0] - t * dx, point[1] - a[1] - t * dy)


def boundary_distance(point, ring):
    return min(
        segment_distance(point, a, b) for a, b in zip(ring, ring[1:], strict=False)
    )


def disk_inside(point, radius, polygon):
    return (
        point_in_ring(point, polygon[0])
        and boundary_distance(point, polygon[0]) > radius
        and all(
            not point_in_ring(point, hole) and boundary_distance(point, hole) > radius
            for hole in polygon[1:]
        )
    )


def disk_intersects(point, radius, polygon):
    within = point_in_ring(point, polygon[0]) and not any(
        point_in_ring(point, hole) for hole in polygon[1:]
    )
    return within or any(boundary_distance(point, ring) <= radius for ring in polygon)


def road_buffer(tags):
    # Width is a cartographic safety buffer, not a measured road width.
    if tags.get("highway") in ("footway", "path", "steps", "cycleway", "pedestrian"):
        return 2.0
    if tags.get("highway") in ("motorway", "trunk", "primary", "secondary"):
        return 10.0
    return 5.0


def bounds_of(line):
    xs, ys = zip(*line, strict=True)
    return min(xs), min(ys), max(xs), max(ys)


def near_bounds(point, radius, bounds):
    return (
        bounds[0] - radius <= point[0] <= bounds[2] + radius
        and bounds[1] - radius <= point[1] <= bounds[3] + radius
    )


def canopy_samples(
    features, bbox, origin, *, spacing=10.0, max_canopies=1500, primary_exclusions=()
):
    """Deterministic artistic crowns, constrained by source polygons and exclusions.

    Original tree points are retained separately. Neither radius nor height is
    claimed as measured. A green park outline alone never supplies tree positions.
    """
    validate_bbox(bbox)
    if (
        type(spacing) not in (int, float)
        or not math.isfinite(spacing)
        or not 6 <= spacing <= 30
        or type(max_canopies) is not int
        or not 1 <= max_canopies <= 6000
    ):
        raise ValueError("Invalid artistic canopy spacing or budget")
    for bounds in primary_exclusions:
        validate_bbox(bounds)
    if not isinstance(origin, (list, tuple)) or len(origin) != 2:
        raise ValueError("Expected origin longitude and latitude")
    coordinate({"lon": origin[0], "lat": origin[1]})

    def project(point):
        return local_point(point[0], point[1], 0, *origin)[:2]

    def project_polygon(polygon):
        return [[project(point) for point in ring] for ring in polygon]

    forbidden, roads, woods, mapped = [], [], [], []
    for feature in features:
        geometry, properties = feature["geometry"], feature["properties"]
        kind = properties["source_kind"]
        parts = [project_polygon(polygon) for polygon in polygons(geometry)]
        if kind.startswith("exclusion_") or kind == "mapped_grass":
            forbidden.extend(parts)
        if kind == "exclusion_road" and geometry["type"] == "LineString":
            roads.append(
                (
                    [project(point) for point in geometry["coordinates"]],
                    road_buffer(properties["tags"]),
                )
            )
        elif kind == "mapped_woodland":
            woods.extend((feature["id"], polygon) for polygon in parts)
        elif kind == "mapped_tree" and geometry["type"] == "Point":
            mapped.append(
                (
                    feature["id"],
                    geometry["coordinates"],
                    project(geometry["coordinates"]),
                )
            )
    for west, south, east, north in primary_exclusions:
        forbidden.append(
            project_polygon(
                [
                    [
                        [west, south],
                        [east, south],
                        [east, north],
                        [west, north],
                        [west, south],
                    ]
                ]
            )
        )
    west, south, east, north = bbox
    study = project_polygon(
        [[[west, south], [east, south], [east, north], [west, north], [west, south]]]
    )
    samples, accepted, stats = [], [], Counter()
    forbidden = [(polygon, bounds_of(polygon[0])) for polygon in forbidden]
    roads = [(line, buffer, bounds_of(line)) for line, buffer in roads]
    study_bounds = bounds_of(study[0])

    def allowed(point, radius):
        return (
            disk_inside(point, radius, study)
            and not any(
                disk_intersects(point, radius + 1.0, polygon)
                for polygon, bounds in forbidden
                if near_bounds(point, radius + 1.0, bounds)
            )
            and not any(
                boundary_distance(point, line) < radius + buffer
                for line, buffer, bounds in roads
                if near_bounds(point, radius + buffer, bounds)
            )
        )

    def add(source_id, point, radius, height, placement, identity, lonlat=None):
        if not allowed(point, radius):
            stats["excluded_by_footprint_or_buffer"] += 1
            return
        if placement == "artistic_woodland_fill" and any(
            math.dist(point, prior) < 0.8 * (radius + previous_radius)
            for prior, previous_radius in accepted
        ):
            stats["excluded_near_existing_crown"] += 1
            return
        if len(samples) >= max_canopies:
            stats["omitted_for_canopy_budget"] += 1
            return
        lon, lat = (
            lonlat
            if lonlat is not None
            else local_to_geodetic([*point, 0], *origin)[:2]
        )
        samples.append(
            {
                "id": identity,
                "source_id": source_id,
                "lon": lon,
                "lat": lat,
                "radius_m": radius,
                "height_m": height,
                "placement": placement,
                "dimensions_measured": False,
                "canopy_representation": "artistic",
            }
        )
        accepted.append((point, radius))

    for source_id, lonlat, point in mapped:
        add(source_id, point, 2.25, 7.0, "mapped_tree", source_id + ":crown", lonlat)
    candidates = 0
    for source_id, polygon in woods:
        xs, ys = zip(*polygon[0], strict=True)
        for ix in range(
            math.floor(max(min(xs), study_bounds[0]) / spacing),
            math.ceil(min(max(xs), study_bounds[2]) / spacing) + 1,
        ):
            for iy in range(
                math.floor(max(min(ys), study_bounds[1]) / spacing),
                math.ceil(min(max(ys), study_bounds[3]) / spacing) + 1,
            ):
                candidates += 1
                if candidates > 200_000:
                    raise ValueError("Artistic canopy candidate budget exceeded")
                seed = hashlib.sha256(f"{source_id}:{ix}:{iy}".encode()).digest()
                point = [
                    (ix + (seed[0] / 255 - 0.5) * 0.5) * spacing,
                    (iy + (seed[1] / 255 - 0.5) * 0.5) * spacing,
                ]
                radius, height = 2.6 + seed[2] / 255 * 1.2, 7.0 + seed[3] / 255 * 4.0
                if disk_inside(point, radius, polygon):
                    add(
                        source_id,
                        point,
                        radius,
                        height,
                        "artistic_woodland_fill",
                        f"{source_id}:fill:{ix}:{iy}",
                    )
    return samples, {
        **dict(stats),
        "candidate_count": candidates,
        "sample_count": len(samples),
        "max_canopies": max_canopies,
        "spacing_m": spacing,
        "primary_source_exclusion_bboxes": list(primary_exclusions),
        "counts_by_placement": dict(Counter(item["placement"] for item in samples)),
        "method": "Stable hashed metric grid inside mapped woodland only. Complete crown disks avoid polygon edges/holes, mapped grass, buildings, water, play areas and conservative road buffers. Mapped tree coordinates unchanged; all crown dimensions artistic. No tree inference from park boundaries.",
    }


def write_package(
    output,
    payload,
    raw,
    metadata,
    query,
    bbox,
    origin,
    *,
    spacing=10.0,
    primary_exclusions=(),
    public_reference=None,
    max_canopies=1500,
    max_features=MAX_FEATURES,
    max_coordinates=MAX_COORDINATES,
):
    if (
        not isinstance(metadata, dict)
        or metadata.get("endpoint") != ENDPOINT
        or metadata.get("sha256") != digest(raw)
        or metadata.get("bytes") != len(raw)
        or metadata.get("query_sha256") != digest(query.encode())
        or json.loads(raw) != payload
    ):
        raise ValueError("Vegetation payload and acquisition provenance disagree")
    if public_reference is not None and (
        not isinstance(public_reference, str)
        or not public_reference
        or "\\" in public_reference
        or ":" in public_reference
        or PurePosixPath(public_reference).is_absolute()
        or ".." in PurePosixPath(public_reference).parts
    ):
        raise ValueError("Public data reference must be a relative repository path")
    features, skipped = normalize(
        payload, max_features=max_features, max_coordinates=max_coordinates
    )
    samples, representation = canopy_samples(
        features,
        bbox,
        origin,
        spacing=spacing,
        primary_exclusions=primary_exclusions,
        max_canopies=max_canopies,
    )
    output = Path(output)
    if output.is_symlink() or (
        output.exists() and (not output.is_dir() or not (output / MARKER).is_file())
    ):
        raise ValueError("Refusing to replace an unknown vegetation directory")
    backup = output.with_name(f".{output.name}-previous")
    if backup.exists():
        raise ValueError("Vegetation backup already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".vegetation-", dir=output.parent))
    try:
        (stage / MARKER).write_text("Bounded OSM vegetation v1\n")
        (stage / "source-response.json").write_bytes(raw)
        (stage / "query.overpassql").write_text(query)
        notice = f"{ATTRIBUTION}\nOpenStreetMap data: Open Database License 1.0 ({LICENSE}).\nSource and adapted geolocated feature/placement data retain ODbL terms, not the code's MIT licence.\nAll crown dimensions and woodland fill positions are artistic interpretations, not surveyed trees.\nSource completeness, map accuracy and current tree condition are unverified. No endorsement.\n"
        (stage / "NOTICE.txt").write_text(notice)
        manifest = {
            "schema_version": 1,
            "kind": "source-vegetation",
            "status": "complete",
            "crs": "EPSG:4326",
            "bbox": list(bbox),
            "origin_lonlat": list(origin),
            "source": {
                **metadata,
                "source_url": "https://www.openstreetmap.org/",
                "attribution": ATTRIBUTION,
                "license": {"id": "ODbL-1.0", "url": LICENSE},
                "timestamp_osm_base": payload.get("osm3s", {}).get(
                    "timestamp_osm_base"
                ),
                "raw_path": "source-response.json",
                "query_path": "query.overpassql",
            },
            "source_coverage_verified": False,
            "survey_verified": False,
            "ai_styling": False,
            "artistic_vegetation": True,
            "features": features,
            "canopy_samples": samples,
            "representation": representation,
            "feature_counts": dict(
                Counter(feature["properties"]["source_kind"] for feature in features)
            ),
            "skipped_features": skipped,
            "notice": "NOTICE.txt",
            "public_data_reference": public_reference,
            "limitations": "Community mapped vegetation is not an authoritative tree inventory. Missing polygons or points do not prove absence. Road buffers are conservative cartographic exclusions, not surveyed edges. Park boundaries are reference-only. Original source geometries retained; only crown samples are restricted to bbox. Crown radius, height and woodland fill positions are artistic and must not be labeled measured.",
        }
        manifest["assets"] = [
            {
                "path": name,
                "bytes": (stage / name).stat().st_size,
                "sha256": digest((stage / name).read_bytes()),
            }
            for name in ("source-response.json", "query.overpassql", "NOTICE.txt")
        ]
        (stage / "manifest.json").write_bytes(json_bytes(manifest))
        if output.exists():
            output.rename(backup)
        try:
            stage.rename(output)
        except BaseException:
            if backup.exists():
                backup.rename(output)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        return manifest
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def write_public_bundle(manifest, query, output):
    """Publish the actual interpreted database, not contributor account metadata."""
    output = Path(output)
    if output.exists():
        raise ValueError("Public bundle directory must be new")
    output.mkdir(parents=True)
    public = json.loads(json.dumps(manifest))
    public.pop("assets")
    public["source"].pop("raw_path")
    public["source"]["raw_response_included"] = False
    public["distribution"] = (
        "Adapted OpenStreetMap feature and artistic placement database under ODbL 1.0. The raw response contains contributor account metadata and is not included. Geometry, source feature IDs, versions, timestamps and representation are retained."
    )
    blob = json.dumps(
        public, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()
    (output / "vegetation.json.gz").write_bytes(gzip.compress(blob, mtime=0))
    (output / "query.overpassql").write_text(query)
    (output / "NOTICE.txt").write_text(
        f"{ATTRIBUTION}\nThis adapted geographic and artistic-placement database is available under Open Database License 1.0 (ODbL): {LICENSE}\nhttps://www.openstreetmap.org/copyright\nThe repository's MIT licence covers code, not this data.\nCrown dimensions and woodland fill positions are artistic, not measured trees.\nContributor user names, numeric user IDs and changeset IDs are not distributed.\nNo endorsement, no guarantee of accuracy or completeness.\n"
    )
    (output / "README.txt").write_text(
        "Use gzip to decompress vegetation.json.gz, a UTF-8 JSON source-vegetation manifest.\nfeatures retain mapped geometries and source identities. canopy_samples are the exact deterministic artistic placements used for rendering, distinct from mapped source features.\nThe source query and its raw response hash identify acquisition. No raw contributor account metadata is included.\nNOTICE.txt contains attribution and the data licence link. SHA256SUMS verifies every data/notice file.\n"
    )
    (output / "SHA256SUMS").write_text(
        "".join(
            f"{digest(path.read_bytes())}  {path.name}\n"
            for path in sorted(output.iterdir())
        )
    )
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        default=[139.6873, 35.6860, 139.6961, 35.6932],
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
    )
    parser.add_argument("--origin", nargs=2, type=float, default=[139.6917, 35.6896])
    parser.add_argument("--output", type=Path, default=Path("data/vegetation"))
    parser.add_argument(
        "--cache", type=Path, default=Path(".cache/vegetation-downloads")
    )
    parser.add_argument("--max-bytes", type=int, default=5_000_000)
    parser.add_argument("--spacing", type=float, default=10.0)
    parser.add_argument(
        "--exclude-bbox", nargs=4, type=float, action="append", default=[]
    )
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--public-bundle", type=Path)
    parser.add_argument("--public-reference")
    args = parser.parse_args()
    query = query_for(args.bbox)
    raw, metadata, downloaded = fetch_response(
        query, args.cache, max_bytes=args.max_bytes, offline=args.offline
    )
    result = write_package(
        args.output,
        json.loads(raw),
        raw,
        metadata,
        query,
        args.bbox,
        args.origin,
        spacing=args.spacing,
        primary_exclusions=args.exclude_bbox,
        public_reference=args.public_reference,
    )
    if args.public_bundle:
        write_public_bundle(result, query, args.public_bundle)
    print(
        json.dumps(
            {
                "download_bytes": downloaded,
                "source_bytes": len(raw),
                "feature_counts": result["feature_counts"],
                "representation": result["representation"],
                "skipped_features": result["skipped_features"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

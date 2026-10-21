"""Explicit, attributed viewer metadata for bounded chunk-pyramid releases."""

import math
from urllib.parse import urlsplit


def text(value, limit=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("Expected bounded nonempty viewer metadata text")
    return value


def validate_metadata(value):
    if not isinstance(value, dict):
        raise ValueError("Viewer metadata must be an object")
    allowed = {
        "title",
        "attribution",
        "coverage",
        "source_edition",
        "ai_styling",
        "modifications",
        "limitations",
        "notice_text",
        "place",
        "layer_labels",
    }
    if set(value) - allowed:
        raise ValueError("Unknown viewer metadata fields")
    if type(value.get("ai_styling")) is not bool:
        raise ValueError("Explicit AI styling provenance is required")
    if not isinstance(value.get("attribution"), list) or not value["attribution"]:
        raise ValueError("Source attribution is required")
    credits = []
    for credit in value["attribution"]:
        if not isinstance(credit, dict) or set(credit) != {"label", "url"}:
            raise ValueError("Invalid attribution record")
        url = text(credit["url"])
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Attribution requires a public HTTPS URL")
        credits.append({"label": text(credit["label"]), "url": url})
    coverage = value.get("coverage")
    if not isinstance(coverage, dict) or set(coverage) != {"label", "bbox"}:
        raise ValueError("Coverage label and geographic bounds are required")
    bbox = coverage["bbox"]
    if (
        not isinstance(bbox, list)
        or len(bbox) != 4
        or any(
            type(item) not in (int, float) or not math.isfinite(item) for item in bbox
        )
    ):
        raise ValueError("Invalid geographic coverage bounds")
    west, south, east, north = bbox
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("Invalid geographic coverage bounds")
    result = {
        "title": text(value.get("title")),
        "source_edition": text(value.get("source_edition")),
        "ai_styling": value["ai_styling"],
        "attribution": credits,
        "coverage": {"label": text(coverage["label"]), "bbox": bbox},
        "notice_text": text(value.get("notice_text"), 100_000),
    }
    for field in ("modifications", "limitations"):
        if field in value:
            result[field] = text(value[field])
    if "place" in value:
        place = value["place"]
        if not isinstance(place, dict) or set(place) - {"name", "name_ja"}:
            raise ValueError("Invalid place label")
        result["place"] = {"name": text(place.get("name"))}
        if "name_ja" in place:
            result["place"]["name_ja"] = text(place["name_ja"])
    labels = value.get("layer_labels", {})
    if not isinstance(labels, dict):
        raise ValueError("Layer labels must be an object")
    result["layer_labels"] = {
        text(key, 96): text(label) for key, label in labels.items()
    }
    return result


def manifest_for(metadata, settings, sources, release_id):
    metadata = validate_metadata(metadata)
    labels = metadata.pop("layer_labels")
    metadata.pop("notice_text")
    if set(labels) - set(settings["layers"]):
        raise ValueError("Viewer labels refer to an absent layer")
    bounds = settings["bounds_px"]
    if (
        len(settings["layers"]) > 8
        or len(sources) > 100_000
        or bounds[2] - bounds[0] > 100_000_000
        or bounds[3] - bounds[1] > 100_000_000
    ):
        raise ValueError("Release exceeds the browser manifest safety limits")
    metadata["coverage"]["rectangles_px"] = [source["bounds_px"] for source in sources]
    return {
        **metadata,
        "schema_version": 1,
        "id": release_id,
        "width": bounds[2] - bounds[0],
        "height": bounds[3] - bounds[1],
        "bounds_px": bounds,
        "tile_size": settings["tile_size"],
        "overlap": settings["overlap"],
        "format": "webp",
        "layers": [
            {"id": layer, "label": labels.get(layer, layer), "dzi": f"{layer}.dzi"}
            for layer in settings["layers"]
        ],
        "notice": "NOTICE.txt",
        "source_coverage_verified": False,
        "artwork_approved": False,
    }

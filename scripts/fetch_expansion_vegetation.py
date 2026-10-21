"""Four capped OSM vegetation queries with mixed-snapshot provenance and strict deduplication."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

from atlas import load_config
from fetch_vegetation import (
    ENDPOINT,
    digest,
    fetch_response,
    json_bytes,
    query_for,
    validate_bbox,
    write_package,
    write_public_bundle,
)


def quadrants(bbox):
    west, south, east, north = validate_bbox(bbox)
    middle_x, middle_y = (west + east) / 2, (south + north) / 2
    return [
        [west, south, middle_x, middle_y],
        [middle_x, south, east, middle_y],
        [west, middle_y, middle_x, north],
        [middle_x, middle_y, east, north],
    ]


def snapshot_query(bbox, timestamp=None):
    query = query_for(bbox)
    if timestamp is None:
        return query
    if not isinstance(timestamp, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp
    ):
        raise ValueError("Expected an exact OSM UTC snapshot timestamp")
    return query.replace("[out:json]", f'[out:json][date:"{timestamp}"]', 1)


def merge_payloads(payloads, timestamp):
    if len(payloads) != 4:
        raise ValueError("Exactly four bounded quadrant responses are required")
    elements = {}
    for payload in payloads:
        if payload.get("remark") or not isinstance(payload.get("elements"), list):
            raise ValueError("Incomplete expansion response")
        for item in payload["elements"]:
            key = (item.get("type"), item.get("id"))
            if key[0] not in ("node", "way", "relation") or type(key[1]) is not int:
                raise ValueError("Invalid OSM source identity")
            if key in elements and elements[key] != item:
                raise ValueError(
                    "Conflicting snapshot versions or geometry for one OSM identity"
                )
            elements[key] = item
            if len(elements) > 90000:
                raise ValueError("Expansion source exceeds 90000 original features")
    return {
        "version": 0.6,
        "generator": "isometric-tokyo strict four-query merge, not an upstream response",
        "osm3s": {"timestamp_osm_base": timestamp},
        "elements": [elements[key] for key in sorted(elements)],
    }


def primary_exclusions(context_path):
    context = json.loads(context_path.read_text())
    regions = [
        [math.degrees(value) for value in tile["bounding_volume"]["region"][:4]]
        for tile in context["tiles"]
        if tile["context_type"] == "veg"
    ]
    if not regions:
        return []
    # Conservative union box avoids rendering inferred OSM crowns over the
    # official PLATEAU vegetation area, just as the native pilot did.
    return [
        [
            min(r[0] for r in regions),
            min(r[1] for r in regions),
            max(r[2] for r in regions),
            max(r[3] for r in regions),
        ]
    ]


def acquire(
    config_path,
    output,
    cache,
    *,
    public_bundle=None,
    context_path=None,
    prior_network_charge=0,
    audit_path=None,
    offline=False,
):
    config = load_config(config_path)
    queries, payloads, receipts = [], [], []
    if (
        type(prior_network_charge) is not int
        or not 0 <= prior_network_charge <= 100_000_000
    ):
        raise ValueError(
            "Prior network charge must fit the finite 100 MB acquisition cap"
        )
    network_bytes = 0
    ledger = {
        "prior_network_response_body_charge": prior_network_charge,
        "maximum_network_response_body_bytes": 100_000_000,
        "attempts": [],
    }

    def save_ledger():
        if audit_path:
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            audit_path.write_bytes(json_bytes(ledger))

    for bounds in quadrants(config.source_bounds_wsen):
        query = snapshot_query(bounds)
        # The adapter reads one extra byte to detect overflow. Reserve that byte
        # inside the approved 25 MB per-request response-body ceiling.
        key = digest((ENDPOINT + "\n" + query).encode())
        cached = (Path(cache) / (key + ".json")).exists() and (
            Path(cache) / (key + ".meta.json")
        ).exists()
        available = (
            25_000_000
            if cached
            else min(25_000_000, 100_000_000 - prior_network_charge - network_bytes)
        )
        if available < 2:
            raise ValueError("Expansion OSM response-body budget exhausted")
        attempt = {
            "query_sha256": digest(query.encode()),
            "maximum_body_bytes": available,
            "status": "pending",
        }
        ledger["attempts"].append(attempt)
        save_ledger()
        try:
            raw, metadata, downloaded = fetch_response(
                query, cache, max_bytes=available - 1, offline=offline
            )
        except Exception:
            failure = Path(cache) / (
                digest((ENDPOINT + "\n" + query).encode()) + ".failure.json"
            )
            charge = (
                json.loads(failure.read_text())["bytes"]
                if failure.exists()
                else available
            )
            attempt.update(status="failed", charged_response_body_bytes=charge)
            ledger["charged_response_body_bytes"] = (
                prior_network_charge + network_bytes + charge
            )
            save_ledger()
            raise
        network_bytes += downloaded
        attempt.update(
            status="complete",
            charged_response_body_bytes=downloaded,
            source_bytes=len(raw),
        )
        ledger["charged_response_body_bytes"] = prior_network_charge + network_bytes
        save_ledger()
        if prior_network_charge + network_bytes > 100_000_000:
            raise ValueError(
                "Expansion OSM acquisition exceeds 100 MB response-body limit"
            )
        payload = json.loads(raw)
        timestamp = payload.get("osm3s", {}).get("timestamp_osm_base")
        if timestamp is None:
            raise ValueError("Every response must identify its actual OSM snapshot")
        snapshot_query(bounds, timestamp)
        queries.append(query)
        payloads.append(payload)
        receipts.append(
            {
                **metadata,
                "bounds_wsen": bounds,
                "query": query,
                "actual_snapshot_timestamp": timestamp,
                "requested_historical_snapshot": False,
            }
        )
        print(f"QUADRANT_READY {len(payloads)} {len(raw)} bytes", flush=True)
    timestamps = [receipt["actual_snapshot_timestamp"] for receipt in receipts]
    payload = merge_payloads(payloads, None)
    payload["osm3s"] = {"snapshot_range": [min(timestamps), max(timestamps)]}
    raw = json_bytes(payload)
    query_bundle = "\n\n".join(
        f"// Independently executed quadrant {i + 1}\n{query}"
        for i, query in enumerate(queries)
    )
    metadata = {
        "endpoint": ENDPOINT,
        "sha256": digest(raw),
        "bytes": len(raw),
        "query_sha256": digest(query_bundle.encode()),
        "fetched_at": max(receipt["fetched_at"] for receipt in receipts),
        "acquisition_method": "Four bounded current-snapshot queries with strict duplicate version and geometry equality. This is not one single-instant database snapshot. source-response.json is a derived merge, not one upstream response. query.overpassql is an annotated bundle of independently executed queries. Historical snapshot querying was rejected by upstream memory limits and was not used for accepted data.",
        "component_sources": receipts,
        "network_response_body_bytes": network_bytes,
        "prior_network_response_body_charge": prior_network_charge,
        "total_charged_network_response_body_bytes": prior_network_charge
        + network_bytes,
        "maximum_network_response_body_bytes": 100_000_000,
        "automatic_retries": 0,
        "snapshot_range": [min(timestamps), max(timestamps)],
        "single_instant_snapshot": False,
        "primary_context_manifest_sha256": digest(context_path.read_bytes())
        if context_path
        else None,
    }
    result = write_package(
        output,
        payload,
        raw,
        metadata,
        query_bundle,
        config.source_bounds_wsen,
        config.origin_lonlat,
        max_canopies=6000,
        max_features=90000,
        max_coordinates=900000,
        primary_exclusions=primary_exclusions(context_path) if context_path else (),
    )
    if public_bundle:
        write_public_bundle(result, query_bundle, public_bundle)
    return {
        "network_bytes": network_bytes,
        "features": len(result["features"]),
        "representation": result["representation"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path("configs/shinjuku-expansion-source.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("data/vegetation-expansion-v1")
    )
    parser.add_argument(
        "--cache", type=Path, default=Path(".cache/vegetation-downloads")
    )
    parser.add_argument("--public-bundle", type=Path)
    parser.add_argument("--prior-network-charge", type=int, default=0)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument(
        "--audit",
        type=Path,
        default=Path(".cache/vegetation-expansion-acquisition.json"),
    )
    parser.add_argument(
        "--context", type=Path, default=Path("data/context-expansion-v1/manifest.json")
    )
    args = parser.parse_args()
    print(
        json.dumps(
            acquire(
                args.config,
                args.output,
                args.cache,
                public_bundle=args.public_bundle,
                context_path=args.context,
                prior_network_charge=args.prior_network_charge,
                audit_path=args.audit,
                offline=args.offline,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

import gzip
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from fetch_vegetation import (
    ENDPOINT,
    canopy_samples,
    classify,
    disk_inside,
    disk_intersects,
    fetch_response,
    geometry_for,
    normalize,
    query_for,
    validate_bbox,
    write_package,
    write_public_bundle,
)
from geo import local_point, local_to_geodetic

ORIGIN = [139.6917, 35.6896]
BBOX = [139.689, 35.687, 139.695, 35.692]


def lonlat(x, y):
    return list(local_to_geodetic([x, y, 0], *ORIGIN)[:2])


def ring(x0, y0, x1, y1):
    return [lonlat(x, y) for x, y in [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]]


def feature(identifier, kind, coordinates, geometry="Polygon", tags=None):
    return {
        "type": "Feature",
        "id": identifier,
        "properties": {
            "source_id": identifier,
            "source_kind": kind,
            "tags": tags or {},
        },
        "geometry": {"type": geometry, "coordinates": coordinates},
    }


def way(identifier, tags, coordinates):
    return {
        "type": "way",
        "id": identifier,
        "tags": tags,
        "geometry": [{"lon": lon, "lat": lat} for lon, lat in coordinates],
    }


class Response(io.BytesIO):
    def __init__(self, blob, length=None):
        super().__init__(blob)
        self.headers = {} if length is None else {"Content-Length": str(length)}


class VegetationTests(unittest.TestCase):
    def test_bbox_and_query_have_hard_bounds(self):
        self.assertEqual(validate_bbox(BBOX), BBOX)
        query = query_for(BBOX)
        self.assertIn("[timeout:45]", query)
        self.assertIn('node["natural"="tree"]', query)
        self.assertIn('way["highway"]', query)
        self.assertIn('relation["building"]', query)
        for invalid in (
            [0, 0, 1, 1],
            [1, 0, 0, 1],
            [0, 0, True, 1],
            [0, 0, 0.01, float("nan")],
        ):
            with self.subTest(bounds=invalid), self.assertRaises(ValueError):
                validate_bbox(invalid)

    def test_cache_is_checked_and_offline_does_not_make_requests(self):
        blob = b'{"elements":[]}'
        with tempfile.TemporaryDirectory() as directory:
            calls = []

            def opener(request, timeout):
                calls.append((request.full_url, timeout))
                return Response(blob, len(blob))

            first = fetch_response("query", directory, opener=opener)
            second = fetch_response("query", directory, offline=True)
            self.assertEqual(first[0], second[0])
            self.assertEqual(second[2], 0)
            self.assertEqual(calls, [(ENDPOINT, 60)])
            with self.assertRaises(ValueError):
                fetch_response("different", directory, offline=True)
            for path in Path(directory).glob("*.json"):
                if not path.name.endswith(".meta.json"):
                    path.write_bytes(b'{"elements":[0]}')
            with self.assertRaises(ValueError):
                fetch_response("query", directory, offline=True)

    def test_download_budget_and_partial_results_are_failures(self):
        for blob, length, budget in [
            (b"x" * 11, None, 10),
            (b"x", 20, 10),
            (b"x", 2, 10),
            (b'{"remark":"runtime error","elements":[]}', None, 100),
        ]:
            with (
                self.subTest(blob=blob),
                tempfile.TemporaryDirectory() as directory,
                self.assertRaises(ValueError),
            ):
                fetch_response(
                    "q",
                    directory,
                    max_bytes=budget,
                    opener=lambda request, timeout, blob=blob, length=length: Response(
                        blob, length
                    ),
                )
                self.assertFalse(list(Path(directory).iterdir()))

    def test_source_classes_never_treat_park_as_woodland(self):
        self.assertEqual(classify({"leisure": "park"}), "park_boundary")
        self.assertEqual(classify({"landuse": "grass"}), "mapped_grass")
        self.assertEqual(
            classify({"landuse": "forest", "barrier": "hedge"}), "mapped_scrub"
        )
        self.assertEqual(
            classify({"landuse": "forest", "building": "yes"}), "exclusion_building"
        )

    def test_source_points_keep_coordinates_but_not_survey_claims_or_user_metadata(
        self,
    ):
        point = {
            "type": "node",
            "id": 123,
            "lat": 35.689,
            "lon": 139.691,
            "version": 2,
            "user": "test-user",
            "uid": 456,
            "changeset": 789,
            "tags": {"natural": "tree", "source": "survey"},
        }
        features, skipped = normalize({"elements": [point]})
        self.assertFalse(skipped)
        self.assertEqual(features[0]["geometry"]["coordinates"], [139.691, 35.689])
        self.assertEqual(features[0]["properties"]["source_kind"], "mapped_tree")
        self.assertFalse(features[0]["properties"]["survey_verified"])
        self.assertNotIn("test-user", json.dumps(features))
        self.assertNotIn('"uid"', json.dumps(features))
        self.assertNotIn('"changeset"', json.dumps(features))

    def test_multipolygon_stitches_reversed_ways_and_preserves_holes(self):
        outer, inner = ring(-100, -100, 100, 100), ring(-20, -20, 20, 20)

        def member(points, role):
            return {
                "type": "way",
                "role": role,
                "geometry": [{"lon": x, "lat": y} for x, y in points],
            }

        element = {
            "type": "relation",
            "tags": {"type": "multipolygon"},
            "members": [
                member(outer[:3], "outer"),
                member(outer[2:][::-1], "outer"),
                member(inner, "inner"),
            ],
        }
        geometry = geometry_for(element, "mapped_woodland")
        self.assertEqual(geometry["type"], "MultiPolygon")
        self.assertEqual(geometry["coordinates"], [[outer, inner]])

    def test_invalid_exclusions_fail_closed_and_unsupported_green_is_reported(self):
        invalid = way(123, {"building": "yes"}, ring(0, 0, 10, 10)[:-1])
        with self.assertRaises(ValueError):
            normalize({"elements": [invalid]})
        invalid["tags"] = {"landuse": "forest"}
        features, skipped = normalize({"elements": [invalid]})
        self.assertFalse(features)
        self.assertEqual(skipped[0]["source_id"], "osm:way/123")
        with self.assertRaises(ValueError):
            normalize({"elements": [invalid, invalid]})

    def test_building_relations_use_explicit_conservative_bounds(self):
        source = {
            "type": "relation",
            "id": 1,
            "tags": {"type": "building", "building": "yes"},
            "bounds": {
                "minlon": 139.69,
                "minlat": 35.68,
                "maxlon": 139.691,
                "maxlat": 35.681,
            },
        }
        features, skipped = normalize({"elements": [source]})
        self.assertFalse(skipped)
        self.assertEqual(
            features[0]["properties"]["geometry_interpretation"],
            "conservative_building_relation_bounds",
        )
        self.assertEqual(features[0]["geometry"]["coordinates"][0][0], [139.69, 35.68])

    def test_disk_constraints_protect_holes_and_edges(self):
        polygon = [
            [[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]],
            [[40, 40], [60, 40], [60, 60], [40, 60], [40, 40]],
        ]
        self.assertTrue(disk_inside([20, 20], 3, polygon))
        self.assertFalse(disk_inside([2, 20], 3, polygon))
        self.assertFalse(disk_inside([50, 50], 3, polygon))
        self.assertFalse(disk_inside([38, 50], 3, polygon))
        self.assertFalse(disk_intersects([50, 50], 3, polygon))
        self.assertTrue(disk_intersects([40, 50], 3, polygon))

    def test_canopies_are_reproducible_constrained_and_explicitly_artistic(self):
        outer, hole = ring(-100, -100, 100, 100), ring(-20, -20, 20, 20)
        buildings = ring(40, 40, 80, 80)
        source = [
            feature("wood", "mapped_woodland", [outer, hole]),
            feature("building", "exclusion_building", [buildings]),
            feature(
                "road",
                "exclusion_road",
                [lonlat(-90, -110), lonlat(-90, 110)],
                "LineString",
                {"highway": "primary"},
            ),
            feature("tree", "mapped_tree", lonlat(-50, -50), "Point"),
        ]
        samples, report = canopy_samples(source, BBOX, ORIGIN)
        self.assertEqual((samples, report), canopy_samples(source, BBOX, ORIGIN))
        self.assertGreater(len(samples), 30)
        self.assertEqual(samples[0]["placement"], "mapped_tree")
        self.assertEqual([samples[0]["lon"], samples[0]["lat"]], lonlat(-50, -50))
        projected = [
            [local_point(*point, 0, *ORIGIN)[:2] for point in r] for r in [outer, hole]
        ]
        for sample in samples:
            self.assertFalse(sample["dimensions_measured"])
            self.assertEqual(sample["canopy_representation"], "artistic")
            point = local_point(sample["lon"], sample["lat"], 0, *ORIGIN)[:2]
            if sample["placement"] == "artistic_woodland_fill":
                self.assertTrue(
                    disk_inside(point, sample["radius_m"] - 0.001, projected)
                )
            self.assertGreater(abs(point[0] + 90), sample["radius_m"] + 9.99)
        self.assertEqual(len({sample["id"] for sample in samples}), len(samples))

    def test_park_and_grass_never_create_tree_positions(self):
        for kind in ("park_boundary", "mapped_grass", "mapped_scrub"):
            samples, report = canopy_samples(
                [feature("area", kind, [ring(-100, -100, 100, 100)])], BBOX, ORIGIN
            )
            self.assertEqual(samples, [])
            self.assertEqual(report["candidate_count"], 0)

    def test_primary_source_exclusion_and_canopy_cap_are_recorded(self):
        source = [feature("wood", "mapped_woodland", [ring(-100, -100, 100, 100)])]
        samples, report = canopy_samples(source, BBOX, ORIGIN, max_canopies=2)
        self.assertEqual(len(samples), 2)
        self.assertGreater(report["omitted_for_canopy_budget"], 0)
        samples, report = canopy_samples(
            source, BBOX, ORIGIN, primary_exclusions=[BBOX]
        )
        self.assertFalse(samples)
        self.assertEqual(report["primary_source_exclusion_bboxes"], [BBOX])

    def test_package_and_public_bundle_keep_hashes_terms_without_raw_accounts(self):
        payload = {
            "osm3s": {"timestamp_osm_base": "2026-10-21T00:00:00Z"},
            "elements": [
                dict(
                    way(1, {"landuse": "forest"}, ring(-40, -40, 40, 40)),
                    user="raw-account-name",
                    uid=321,
                )
            ],
        }
        raw = json.dumps(payload).encode()
        metadata = {
            "endpoint": ENDPOINT,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "query_sha256": hashlib.sha256(b"query").hexdigest(),
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "source"
            manifest = write_package(
                output, payload, raw, metadata, "query", BBOX, ORIGIN
            )
            for asset in manifest["assets"]:
                self.assertEqual(
                    hashlib.sha256((output / asset["path"]).read_bytes()).hexdigest(),
                    asset["sha256"],
                )
            public = write_public_bundle(manifest, "query", Path(directory) / "public")
            data = gzip.decompress((public / "vegetation.json.gz").read_bytes())
            self.assertNotIn(b"raw-account-name", data)
            self.assertFalse(json.loads(data)["source"]["raw_response_included"])
            self.assertIn("ODbL", (public / "NOTICE.txt").read_text())
            self.assertTrue((public / "SHA256SUMS").is_file())
            with self.assertRaises(ValueError):
                write_public_bundle(manifest, "query", public)
            unknown = Path(directory) / "unknown"
            unknown.mkdir()
            with self.assertRaises(ValueError):
                write_package(unknown, payload, raw, metadata, "query", BBOX, ORIGIN)
            with self.assertRaises(ValueError):
                write_package(
                    Path(directory) / "tampered",
                    {"elements": []},
                    raw,
                    metadata,
                    "query",
                    BBOX,
                    ORIGIN,
                )


if __name__ == "__main__":
    unittest.main()

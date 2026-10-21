"""Bounded source-selection proof tests, independent of live upstream services."""

import copy
import io
import itertools
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from atlas import load_config
from geo import local_point
from source_coverage import (
    CoverageCache,
    acquire,
    audit_manifest,
    building_items,
    classify_tile,
    digest,
    enu_geographic_bounds,
    load_plan,
    make_plan,
    orthophoto_support,
    region_enu_bounds,
    sun_slopes,
    terrain_support,
    write_json,
)

ROOT = Path(__file__).resolve().parents[1]
HOST = "https://assets.cms.plateau.reearth.io/fixture/"


class Response(io.BytesIO):
    def __init__(self, blob, known=True):
        super().__init__(blob)
        self.headers = {"Content-Length": str(len(blob))} if known else {}


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = load_config(ROOT / "configs/shinjuku.json")

    def region(self, w=139.6873, s=35.686, e=139.6961, n=35.6932, lo=0, hi=300):
        return [math.radians(v) for v in (w, s, e, n)] + [lo, hi]

    def test_curved_region_contains_dense_interior_not_only_corners(self):
        region = self.region(w=139.67, s=35.67, e=139.72, n=35.72, lo=-100, hi=650)
        bounds = region_enu_bounds(region, self.config.origin_lonlat)
        for x, y, h in itertools.product(range(11), range(11), range(3)):
            point = local_point(
                math.degrees(region[0] + (region[2] - region[0]) * x / 10),
                math.degrees(region[1] + (region[3] - region[1]) * y / 10),
                -100 + 750 * h / 2,
                *self.config.origin_lonlat,
            )
            for i in range(3):
                self.assertLessEqual(bounds[i], point[i])
                self.assertGreaterEqual(bounds[i + 3], point[i])

    def test_region_rejects_nonfinite_wrapped_and_large_bounds(self):
        for region in (
            [0, 0, float("nan"), 1, 0, 1],
            [1, 0, 0, 1, 0, 1],
            [0, 0, 1, 1, 0, 1],
            [0, 0, 0.01, 0.01, 5, 1],
        ):
            with self.assertRaises(ValueError):
                region_enu_bounds(region, self.config.origin_lonlat)
        with self.assertRaises(ValueError):
            enu_geographic_bounds(
                [0, 0, 21000, 100], [0, 100], self.config.origin_lonlat
            )

    def test_sun_cone_slope_bounds_contain_sampled_directions(self):
        slopes, reach = sun_slopes()
        x, y, z = map(math.radians, (20, -25, -35))
        d = (
            math.cos(z) * math.sin(y) * math.cos(x) + math.sin(z) * math.sin(x),
            math.sin(z) * math.sin(y) * math.cos(x) - math.cos(z) * math.sin(x),
            math.cos(y) * math.cos(x),
        )
        norm = math.hypot(d[0], d[1])
        u = (-d[1] / norm, d[0] / norm, 0)
        v = (
            d[1] * u[2] - d[2] * u[1],
            d[2] * u[0] - d[0] * u[2],
            d[0] * u[1] - d[1] * u[0],
        )
        for degrees in (0, 3, 6):
            angle = math.radians(degrees)
            for azimuth in range(360):
                phi = math.radians(azimuth)
                q = [
                    d[i] * math.cos(angle)
                    + (u[i] * math.cos(phi) + v[i] * math.sin(phi)) * math.sin(angle)
                    for i in range(3)
                ]
                for i in (0, 1):
                    self.assertGreaterEqual(q[i] / q[2] + 1e-12, slopes[i][0])
                    self.assertLessEqual(q[i] / q[2] - 1e-12, slopes[i][1])
                self.assertLessEqual(math.hypot(q[0], q[1]) / q[2], reach + 1e-12)
        for rotation, angle in (
            ((90, 0, 0), 12),
            ((0, 0, float("nan")), 12),
            ((0, 0, 0), 31),
        ):
            with self.assertRaises(ValueError):
                sun_slopes(rotation, angle)

    def test_shadow_only_tile_is_not_discarded(self):
        # Synthetic region AABB outside the frame but casting onto its edge.
        slopes, _ = sun_slopes()
        found = False
        for x, y in itertools.product(range(-2000, 2001, 200), repeat=2):
            bounds = [x, y, 200, x + 20, y + 20, 600]
            with patch("source_coverage.region_enu_bounds", return_value=bounds):
                result = classify_tile(
                    self.config,
                    {"bounding_volume": {"region": []}},
                    [0, 0, 2400, 2400],
                    [-100, 650],
                    slopes,
                )
            if not result["direct_view"] and result["shadow_influence"]:
                found = True
                break
        self.assertTrue(found)

    def fixture(self):
        snapshot = {"schema_version": 1, "wards": []}
        blobs = {}
        for number in range(23):
            url = HOST + str(number) + "/tileset.json"
            item = {
                "id": str(number),
                "type": "bldg",
                "format": "CESIUM3DTILES",
                "lod": 2,
                "texture": "TEXTURE",
                "url": url,
            }
            snapshot["wards"].append(
                {
                    "code": str(number),
                    "name_en": "Ward " + str(number),
                    "edition": 2025,
                    "metadata_status": "confirmed",
                    "package": {"license": {"url": "https://example.test/terms"}},
                    "catalog": {"items": [item]},
                }
            )
            region = (
                self.region()
                if number == 0
                else self.region(w=139.8, s=35.8, e=139.81, n=35.81)
            )
            blobs[url] = json.dumps(
                {
                    "asset": {"version": "1.0"},
                    "root": {
                        "boundingVolume": {"region": region},
                        "content": {"uri": "tile.b3dm"},
                    },
                }
            ).encode()
        path = self.root / "inventory.json"
        write_json(path, snapshot)
        cache = CoverageCache(
            self.root / "cache",
            opener=lambda request, timeout: Response(blobs[request.full_url]),
        )
        return snapshot, path, cache, blobs

    def test_plan_is_reproducible_and_never_claims_survey_completeness(self):
        _, path, cache, _ = self.fixture()
        a = make_plan(ROOT / "configs/shinjuku.json", path, cache)
        b = make_plan(ROOT / "configs/shinjuku.json", path, cache)
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(a["tile_count"], 1)
        self.assertEqual(len(a["sources"]), 23)
        self.assertEqual(a["shadow_receiver_height_range_enu_m"], [-100, 650])
        self.assertFalse(a["source_coverage_verified"])
        target = self.root / "plan.json"
        write_json(target, a)
        self.assertEqual(load_plan(target)["id"], a["id"])
        a["tile_count"] = 20
        write_json(target, a)
        with self.assertRaisesRegex(ValueError, "identity"):
            load_plan(target)

    def test_inventory_rejects_duplicate_or_missing_ward(self):
        snapshot, _, _, _ = self.fixture()
        snapshot["wards"][1]["code"] = snapshot["wards"][0]["code"]
        with self.assertRaisesRegex(ValueError, "distinct"):
            building_items(snapshot)
        snapshot["wards"].pop()
        with self.assertRaisesRegex(ValueError, "23-ward"):
            building_items(snapshot)

    def test_byte_budget_checks_cache_stream_unknown_length_and_retries(self):
        url = HOST + "x.b3dm"
        cache = CoverageCache(
            self.root / "cache", 4, opener=lambda request, timeout: Response(b"12345")
        )
        with self.assertRaisesRegex(ValueError, "budget"):
            cache.fetch(url)
        self.assertEqual(cache.network_bytes, 0)
        cache = CoverageCache(
            self.root / "cache",
            4,
            opener=lambda request, timeout: Response(b"12345", False),
        )
        with self.assertRaisesRegex(ValueError, "budget"):
            cache.fetch(url)
        self.assertEqual(cache.network_bytes, 4)

        class Broken(Response):
            def read(self, n):
                if self.tell():
                    raise URLError("interrupted")
                return super().read(min(3, n))

        cache = CoverageCache(
            self.root / "cache", 5, opener=lambda request, timeout: Broken(b"1234")
        )
        with self.assertRaisesRegex(ValueError, "budget"):
            cache.fetch(url)
        self.assertEqual(cache.network_bytes, 3)
        cache = CoverageCache(
            self.root / "cache", 5, opener=lambda request, timeout: Response(b"1234")
        )
        cache.fetch(url)
        cache.fetch(url)
        self.assertEqual(cache.source_bytes, 4)
        (cache.root / (digest(url.encode()) + ".bin")).write_bytes(b"1235")
        with self.assertRaisesRegex(ValueError, "checksum"):
            cache.fetch(url)

    def source_fixture(self):
        tile = {
            "url": HOST + "tile.b3dm",
            "path": "tile.b3dm",
            "bytes": 4,
            "sha256": digest(b"tile"),
            "transform": [1] * 16,
            "bounding_volume": {"region": self.region()},
            "tile_bounding_volume": {"region": self.region()},
        }
        (self.root / "tile.b3dm").write_bytes(b"tile")
        manifest = self.root / "manifest.json"
        write_json(manifest, {"tiles": [tile]})
        plan = {"id": "fixture", "required_tiles": [tile]}
        return tile, manifest, plan

    def test_audit_rejects_metadata_change_and_missing_source(self):
        tile, manifest, plan = self.source_fixture()
        self.assertTrue(audit_manifest(plan, manifest)["tile_selection_complete"])
        altered = copy.deepcopy(tile)
        altered["transform"][0] = 7
        write_json(manifest, {"tiles": [altered]})
        self.assertFalse(audit_manifest(plan, manifest)["tile_selection_complete"])
        write_json(manifest, {"tiles": []})
        result = audit_manifest(plan, manifest)
        self.assertEqual(result["missing_tiles"], 1)
        self.assertFalse(result["source_coverage_verified"])

    def test_audit_rejects_raw_checksum_and_escape(self):
        tile, manifest, plan = self.source_fixture()
        (self.root / "tile.b3dm").write_bytes(b"evil")
        with self.assertRaisesRegex(ValueError, "checksum/path"):
            audit_manifest(plan, manifest)
        tile["path"] = "../escape.b3dm"
        write_json(manifest, {"tiles": [tile]})
        with self.assertRaisesRegex(ValueError, "checksum/path"):
            audit_manifest(plan, manifest)

    def test_failed_acquisition_preserves_previous_output(self):
        _, path, cache, _ = self.fixture()
        plan = make_plan(ROOT / "configs/shinjuku.json", path, cache)
        output = self.root / "output"
        output.mkdir()
        (output / ".coverage-source").touch()
        (output / "sentinel").write_text("old")
        with patch(
            "source_coverage.context_tile", side_effect=ValueError("invalid payload")
        ):
            with self.assertRaisesRegex(ValueError, "invalid payload"):
                acquire(plan, output, cache)
        self.assertEqual((output / "sentinel").read_text(), "old")
        self.assertEqual(list(self.root.glob(".coverage-stage-*")), [])

    def test_duplicate_feature_ids_abort_acquisition(self):
        _, path, cache, _ = self.fixture()
        plan = make_plan(ROOT / "configs/shinjuku.json", path, cache)
        record = {
            "context_type": "bldg",
            "feature_ids": {"gml_id": ["duplicate", "duplicate"]},
            "feature_count": 2,
        }
        with patch("source_coverage.context_tile", return_value=record):
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                acquire(plan, self.root / "output", cache)
        self.assertFalse((self.root / "output").exists())

    def terrain_fixture(self):
        # Generous support contains the real view at a simple constant height.
        vertices = [
            [139.67, 35.67, 60],
            [139.72, 35.67, 60],
            [139.67, 35.72, 60],
            [139.72, 35.72, 60],
        ]
        mesh = {
            "vertices_lon_lat_height": vertices,
            "triangles": [[0, 1, 2], [1, 3, 2]],
        }
        path = self.root / "mesh.json"
        write_json(path, mesh)
        plan = {
            "config": self.config.to_dict(),
            "receiver_height_range_enu_m": [0, 150],
            "view_bounds_px_with_overscan": [-32, -32, 2432, 2432],
        }
        return mesh, path, plan

    def test_terrain_support_checks_topology_heights_and_not_survey_accuracy(self):
        mesh, path, plan = self.terrain_fixture()
        result = terrain_support(plan, path)
        self.assertTrue(result["terrain_surface_support_complete"])
        self.assertFalse(result["source_coverage_verified"])
        plan["receiver_height_range_enu_m"] = [0, 40]
        self.assertFalse(terrain_support(plan, path)["receiver_height_range_verified"])
        mesh["triangles"].pop()
        write_json(path, mesh)
        with self.assertRaisesRegex(ValueError, "open interior"):
            terrain_support(plan, path)

    def test_terrain_rejects_nonmanifold_or_reversed_mesh(self):
        mesh, path, plan = self.terrain_fixture()
        mesh["triangles"][0] = [0, 2, 1]
        write_json(path, mesh)
        with self.assertRaisesRegex(ValueError, "reversed"):
            terrain_support(plan, path)
        mesh["triangles"] = [[0, 1, 2], [0, 1, 2], [1, 3, 2]]
        write_json(path, mesh)
        with self.assertRaisesRegex(ValueError, "nonmanifold"):
            terrain_support(plan, path)

    def test_orthophoto_requires_complete_grid_and_mosaic_hash(self):
        from fetch_ground import tile_bounds
        from PIL import Image

        bounds = list(tile_bounds(0, 0, 0))
        image = Image.new("RGB", (256, 256), (12, 34, 56))
        image.save(self.root / "image.png")
        payload = (self.root / "image.png").read_bytes()
        (self.root / "tile.png").write_bytes(payload)
        tile = {
            "x": 0,
            "y": 0,
            "z": 0,
            "bounds_wsen": bounds,
            "path": "tile.png",
            "bytes": len(payload),
            "sha256": digest(payload),
        }
        data = {
            "zoom": 0,
            "tile_range": [0, 0, 0, 0],
            "tiles": [tile],
            "bounds_wsen": bounds,
            "image": "image.png",
            "image_sha256": digest(payload),
            "image_width": 256,
            "image_height": 256,
        }
        path = self.root / "ground.json"
        write_json(path, data)
        self.assertTrue(
            orthophoto_support(path, [-1, -1, 1, 1])["orthophoto_support_complete"]
        )
        data["tiles"] = []
        write_json(path, data)
        self.assertFalse(
            orthophoto_support(path, [-1, -1, 1, 1])["orthophoto_support_complete"]
        )
        data["tiles"] = [tile]
        Image.new("RGB", (256, 256), (1, 2, 3)).save(self.root / "image.png")
        data["image_sha256"] = digest((self.root / "image.png").read_bytes())
        write_json(path, data)
        with self.assertRaisesRegex(ValueError, "pixels"):
            orthophoto_support(path, [-1, -1, 1, 1])
        data["image_sha256"] = "bad"
        write_json(path, data)
        with self.assertRaisesRegex(ValueError, "mosaic checksum"):
            orthophoto_support(path, [-1, -1, 1, 1])


if __name__ == "__main__":
    unittest.main()

"""Offline contract tests for a stable, independently renderable atlas grid."""

import copy
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from atlas import (
    camera_for_bounds,
    geographic_to_pixel,
    job_fingerprint,
    load_config,
    parse_config,
    pixel_to_world,
    plan_chunks,
    source_footprint_enu,
    validate_bounds,
    world_to_pixel,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs/shinjuku.json"


class AtlasContractTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(CONFIG_PATH)
        self.data = self.config.to_dict()

    def test_sample_camera_matches_recorded_overview(self):
        config = self.config
        self.assertEqual(config.origin_lonlat, (139.6917, 35.6896))
        self.assertAlmostEqual(config.camera_target_enu[2], 65.08777160644532 + 55)
        self.assertEqual(world_to_pixel(config, config.camera_target_enu), (1200, 1200))
        camera = camera_for_bounds(config, config.bounds_px)
        self.assertEqual(camera["resolution_px"], [2400, 2400])
        self.assertEqual(camera["target_enu"], list(config.camera_target_enu))
        self.assertAlmostEqual(camera["ortho_scale_m"], 1260)
        self.assertEqual(camera["sensor_fit"], "HORIZONTAL")

    def test_basis_is_orthonormal_and_correctly_oriented(self):
        vectors = list(self.config.basis.values())
        for i, a in enumerate(vectors):
            for j, b in enumerate(vectors):
                self.assertAlmostEqual(
                    sum(x * y for x, y in zip(a, b, strict=True)), int(i == j)
                )
        target = self.config.camera_target_enu
        for offset, expectation in (
            ((10, 0, 0), (1, 1)),
            ((0, 10, 0), (1, -1)),
            ((0, 0, 10), (0, -1)),
        ):
            point = [x + y for x, y in zip(target, offset, strict=True)]
            pixel = world_to_pixel(self.config, point)
            for value, origin, direction in zip(
                pixel, self.config.pixel_origin, expectation, strict=True
            ):
                if direction == 0:
                    self.assertAlmostEqual(value, origin)
                else:
                    self.assertGreater(direction * (value - origin), 0)

    def test_known_height_roundtrip(self):
        for point in (
            (0, 0, 0),
            (350, -220, 332),
            (-1234.125, 9876.5, 65),
            (55, 88, -10),
        ):
            pixel = world_to_pixel(self.config, point)
            actual = pixel_to_world(self.config, pixel, point[2])
            for a, b in zip(actual, point, strict=True):
                self.assertAlmostEqual(a, b, places=9)

    def test_one_pixel_is_not_a_unique_world_point(self):
        pixel = (1234, 567)
        ground = pixel_to_world(self.config, pixel, 65)
        roof = pixel_to_world(self.config, pixel, 265)
        self.assertNotEqual(ground[:2], roof[:2])
        for point in (ground, roof):
            actual = world_to_pixel(self.config, point)
            for a, b in zip(actual, pixel, strict=True):
                self.assertAlmostEqual(a, b)

    def test_geographic_origin_height(self):
        height = self.config.camera_target_enu[2]
        actual = geographic_to_pixel(self.config, *self.config.origin_lonlat, height)
        for a, b in zip(actual, self.config.pixel_origin, strict=True):
            self.assertAlmostEqual(a, b, places=7)

    def test_overscan_preserves_camera_projection_and_crop(self):
        core = (800, 800, 1600, 1200)
        camera = camera_for_bounds(self.config, core, 32)
        self.assertEqual(camera["render_bounds_px"], [768, 768, 1632, 1232])
        self.assertEqual(camera["resolution_px"], [864, 464])
        self.assertEqual(camera["crop_box_px"], [32, 32, 832, 432])
        self.assertAlmostEqual(
            camera["ortho_scale_m"], 864 / self.config.pixels_per_metre
        )
        center = world_to_pixel(self.config, camera["target_enu"])
        self.assertEqual(center, (1200, 1000))

    def test_points_stay_on_same_global_pixels_for_every_camera(self):
        point = (145.25, -345.5, 233.0)
        global_pixel = world_to_pixel(self.config, point)
        for camera in plan_chunks(self.config)["chunks"]:
            delta = [a - b for a, b in zip(point, camera["target_enu"], strict=True)]
            local_pixel = (
                camera["resolution_px"][0] / 2
                + self.config.pixels_per_metre
                * sum(a * b for a, b in zip(delta, camera["right_enu"], strict=True)),
                camera["resolution_px"][1] / 2
                - self.config.pixels_per_metre
                * sum(a * b for a, b in zip(delta, camera["up_enu"], strict=True)),
            )
            for i in range(2):
                self.assertAlmostEqual(
                    local_pixel[i] + camera["render_bounds_px"][i],
                    global_pixel[i],
                    places=9,
                )

    def test_neighbors_use_identical_basis_scale_and_shared_edge(self):
        a = camera_for_bounds(self.config, (0, 0, 800, 800))
        b = camera_for_bounds(self.config, (800, 0, 1600, 800))
        for key in ("right_enu", "up_enu", "forward_enu", "ortho_scale_m"):
            self.assertEqual(a[key], b[key])
        edge = pixel_to_world(self.config, (800, 400), 90)
        self.assertAlmostEqual(world_to_pixel(self.config, edge)[0], 800)
        # A sample center immediately across the boundary is one global pixel away.
        left = pixel_to_world(self.config, (799.5, 400.5), 90)
        right = pixel_to_world(self.config, (800.5, 400.5), 90)
        self.assertAlmostEqual(
            world_to_pixel(self.config, right)[0]
            - world_to_pixel(self.config, left)[0],
            1,
        )

    def test_source_envelope_contains_visible_geometry_at_every_height(self):
        bounds = (800, 800, 1600, 1600)
        box = source_footprint_enu(self.config, bounds, buffer_m=0)
        for x in (800, 1200, 1600):
            for y in (800, 1200, 1600):
                for z in (-100, 65, 332, 650):
                    point = pixel_to_world(self.config, (x, y), z)
                    self.assertLessEqual(box[0], point[0] + 1e-10)
                    self.assertLessEqual(box[1], point[1] + 1e-10)
                    self.assertGreaterEqual(box[2], point[0] - 1e-10)
                    self.assertGreaterEqual(box[3], point[1] - 1e-10)
        expanded = source_footprint_enu(self.config, bounds, buffer_m=600)
        self.assertEqual(
            expanded, [box[0] - 600, box[1] - 600, box[2] + 600, box[3] + 600]
        )

    def test_plan_covers_without_overlap_or_holes(self):
        plan = plan_chunks(self.config)
        self.assertEqual(plan["chunk_count"], 9)
        self.assertFalse(plan["source_coverage_verified"])
        self.assertEqual(plan["chunks"][0]["id"], "x0_y0")
        self.assertEqual(plan["chunks"][-1]["id"], "x2_y2")
        area = sum(
            (c["bounds_px"][2] - c["bounds_px"][0])
            * (c["bounds_px"][3] - c["bounds_px"][1])
            for c in plan["chunks"]
        )
        self.assertEqual(area, 2400**2)
        for i, a in enumerate(plan["chunks"]):
            for b in plan["chunks"][i + 1 :]:
                aa, bb = a["bounds_px"], b["bounds_px"]
                intersection = max(0, min(aa[2], bb[2]) - max(aa[0], bb[0])) * max(
                    0, min(aa[3], bb[3]) - max(aa[1], bb[1])
                )
                self.assertEqual(intersection, 0)

    def test_negative_partial_extents_stay_on_global_grid(self):
        self.data["bounds_px"] = [-100, -50, 900, 850]
        config = parse_config(self.data)
        chunks = plan_chunks(config)["chunks"]
        self.assertEqual(chunks[0]["id"], "x-1_y-1")
        self.assertEqual(chunks[0]["bounds_px"], [-100, -50, 0, 0])
        self.assertEqual(chunks[4]["bounds_px"], [0, 0, 800, 800])
        self.assertEqual(chunks[-1]["bounds_px"], [800, 800, 900, 850])

    def test_config_identity_is_canonical_and_mutation_safe(self):
        reverse_order = dict(reversed(list(self.data.items())))
        self.assertEqual(
            parse_config(reverse_order).fingerprint, self.config.fingerprint
        )
        self.data["origin_lonlat"][0] = 140
        self.assertEqual(self.config.origin_lonlat[0], 139.6917)
        self.assertNotEqual(
            parse_config(self.data).fingerprint, self.config.fingerprint
        )

    def test_job_identity_includes_source_and_tool_versions(self):
        bounds = self.config.bounds_px
        source = {"buildings": "a" * 64, "terrain": "b" * 64}
        tools = {"blender": "4.5.3", "renderer": "v1"}
        original = job_fingerprint(self.config, bounds, source, tools)
        self.assertEqual(
            original,
            job_fingerprint(
                self.config, bounds, dict(reversed(list(source.items()))), tools
            ),
        )
        self.assertNotEqual(
            original,
            job_fingerprint(
                self.config, bounds, source | {"buildings": "c" * 64}, tools
            ),
        )
        self.assertNotEqual(
            original,
            job_fingerprint(self.config, bounds, source, tools | {"renderer": "v2"}),
        )
        self.assertNotEqual(
            original, job_fingerprint(self.config, (0, 0, 800, 800), source, tools)
        )
        for invalid in ({}, {"buildings": "unknown"}, {"../buildings": "a" * 64}):
            with self.assertRaises(ValueError):
                job_fingerprint(self.config, bounds, invalid, tools)

    def test_invalid_config_is_rejected(self):
        invalid = {
            "schema_version": [0, 2, True, 1.0],
            "atlas_id": ["", "Tokyo", "../tokyo", None],
            "release_id": ["a" * 97, "../release"],
            "origin_lonlat": [[181, 35], [139, 90], [139], [math.nan, 35]],
            "camera_target_enu": [[0, 0, math.inf], [0, 0, 10**10]],
            "camera_offset_enu": [
                [0, 0, 100],
                [10, 10, 0],
                [10, 10, -10],
                [100, 100, 1e-8],
            ],
            "pixels_per_metre": [0, -1, math.inf, True, "2", 10**500, 10001],
            "pixel_origin": [[math.nan, 0], [2**40, 0]],
            "bounds_px": [[0, 0, 0, 10], [0, 0, 10.0, 10], [0, 0, True, 10]],
            "source_bounds_wsen": [[140, 35, 139, 36], [139, 36, 140, 35]],
            "chunk_size_px": [0, -10, True, 800.0, 16385],
            "overscan_px": [-1, 0.5, True, 16385],
            "vertical_range_enu_m": [[650, -100], [0, 0], [0, math.inf]],
            "shadow_buffer_m": [-1, math.inf, 100001],
            "render_samples": [0, True, 0.5],
            "render_seed": [-1, True, 2**31],
        }
        for key, values in invalid.items():
            for value in values:
                data = copy.deepcopy(self.data)
                data[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    parse_config(data)
        for value in (None, [], self.data | {"typo": 1}):
            with self.assertRaises(ValueError):
                parse_config(value)
        self.data.pop("atlas_id")
        with self.assertRaises(ValueError):
            parse_config(self.data)

    def test_json_nan_and_duplicate_fields_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for text in (
                '{"schema_version":1,"schema_version":1}',
                '{"scale":NaN}',
                '{"scale":Infinity}',
            ):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    load_config(path)

    def test_bad_function_inputs_are_rejected(self):
        for bounds in ([0, 0, 1, 0], [0, 0, 1], [0, 0, math.inf, 1]):
            with self.assertRaises(ValueError):
                validate_bounds(bounds)
        with self.assertRaises(ValueError):
            world_to_pixel(self.config, [math.inf, 0, 0])
        with self.assertRaises(ValueError):
            pixel_to_world(self.config, [0, 0], math.nan)
        with self.assertRaises(ValueError):
            source_footprint_enu(self.config, self.config.bounds_px, -1)
        with self.assertRaises(ValueError):
            camera_for_bounds(self.config, self.config.bounds_px, True)
        with self.assertRaises(ValueError):
            plan_chunks(self.config, max_chunks=8)

    def test_cli_validation_and_plan_json(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "plan.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/atlas.py"),
                    "plan",
                    "--config",
                    str(CONFIG_PATH),
                    "--output",
                    str(output),
                ],
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(result.stderr, "")
            self.assertEqual(json.loads(output.read_text())["chunk_count"], 9)
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/atlas.py"),
                    "validate",
                    "--config",
                    str(CONFIG_PATH),
                ],
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertTrue(json.loads(result.stdout)["valid"])


if __name__ == "__main__":
    unittest.main()

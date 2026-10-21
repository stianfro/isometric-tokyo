import copy
import json
import math
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from atlas import load_config, pixel_to_world
from fetch_expansion_vegetation import (
    acquire,
    merge_payloads,
    primary_exclusions,
    quadrants,
    snapshot_query,
)
from fetch_vegetation import canopy_samples, normalize
from render_expansion_source import (
    BOUNDS,
    INDEX,
    canonical_feature_key,
    finalize,
    grid_plan,
    landmark_plan,
    validate_camera,
)
from render_pilot_source import digest, finalize_partition, write_index

ROOT = Path(__file__).resolve().parents[1]


class ExpansionSourceTests(unittest.TestCase):
    def test_feature_overlap_keys_ignore_release_local_numeric_ids(self):
        first = {"id": 12, "kind": "building", "gml_id": "building-one"}
        second = {**first, "id": 32000}
        self.assertEqual(canonical_feature_key(first), canonical_feature_key(second))
        self.assertNotEqual(
            canonical_feature_key(first),
            canonical_feature_key({**first, "gml_id": "building-two"}),
        )

    def test_camera_keeps_original_pixel_grid_and_density(self):
        old = load_config(ROOT / "configs/shinjuku-pilot-source.json")
        new = load_config(ROOT / "configs/shinjuku-expansion-source.json")
        camera = validate_camera(new)
        self.assertEqual(camera["resolution_px"], [5120, 5120])
        self.assertAlmostEqual(camera["ortho_scale_m"], 2100)
        for pixel in ((0, 0), (512, 2051), (3072, 3072), (-1024, 4096)):
            self.assertEqual(
                pixel_to_world(old, pixel, 50), pixel_to_world(new, pixel, 50)
            )
        for field, value in (
            ("pixels_per_metre", 1),
            ("pixel_origin", (2560, 2560)),
            ("render_samples", 32),
            ("bounds_px", (0, 0, 5120, 5120)),
        ):
            with self.assertRaises(ValueError):
                validate_camera(replace(new, **{field: value}))

    def test_global_ids_and_local_core_crops_form_complete_expansion(self):
        chunks = grid_plan()
        self.assertEqual(len(chunks), 25)
        self.assertEqual(chunks[0]["id"], "x-1_y-1")
        self.assertEqual(chunks[0]["bounds_px"], [-1024, -1024, 0, 0])
        self.assertEqual(chunks[0]["crop_bounds_px"], [0, 0, 1024, 1024])
        self.assertEqual(chunks[-1]["context_crop_bounds_px"], [3840, 3840, 5120, 5120])
        self.assertEqual(sum(c["retained_pilot_area"] for c in chunks), 9)
        self.assertEqual(
            {c["id"] for c in chunks if c["retained_pilot_area"]},
            {f"x{x}_y{y}" for x in range(3) for y in range(3)},
        )
        for chunk in chunks:
            self.assertEqual(
                chunk["crop_bounds_px"], [v + 1024 for v in chunk["bounds_px"]]
            )
        for args in ((True,), (1025,), (1024, 1025), (1024, -1)):
            with self.assertRaises(ValueError):
                grid_plan(*args)

    def test_lossless_partition_uses_local_not_global_bounds(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            image = Image.new("RGB", (20, 20))
            for y in range(20):
                for x in range(20):
                    image.putpixel((x, y), (x * 12, y * 12, (x + y) * 6))
            outputs = []
            for name in ("source-reference", "atlas-ink"):
                path = directory / f"{name}.png"
                image.save(path)
                outputs.append({"id": name, "path": path.name, "sha256": digest(path)})
            index = {
                "status": "rendered",
                "upscaled": False,
                "native_resolution_px": [20, 20],
                "outputs": outputs,
                "chunks": grid_plan(4, 2),
            }
            write_index(directory, index, INDEX)
            result = finalize_partition(directory, grid_plan(4, 2), index_name=INDEX)
            self.assertTrue(result["core_reassembly_pixel_exact"])
            for chunk in result["chunks"]:
                with Image.open(
                    directory / chunk["assets"]["source-reference"]["core"]["path"]
                ) as crop:
                    self.assertEqual(
                        crop.tobytes(), image.crop(chunk["crop_bounds_px"]).tobytes()
                    )
            changed = copy.deepcopy(result)
            changed["chunks"][0]["crop_bounds_px"] = [1, 1, 5, 5]
            write_index(directory, changed, INDEX)
            with self.assertRaises(ValueError):
                finalize_partition(directory, grid_plan(4, 2), index_name=INDEX)

    def test_expansion_finalizer_rejects_wrong_dimensions_before_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            write_index(
                directory,
                {
                    "native_resolution_px": [3072, 3072],
                    "global_bounds_px": list(BOUNDS),
                },
                INDEX,
            )
            with self.assertRaises(ValueError):
                finalize(directory, directory / "missing")

    def test_landmarks_are_hints_and_kabukicho_is_outside(self):
        plan = landmark_plan(
            load_config(ROOT / "configs/shinjuku-expansion-source.json")
        )
        self.assertEqual(plan[0]["tile_id"], "x3_y2")
        self.assertFalse(plan[1]["inside_expansion"])
        self.assertEqual(plan[2]["tile_id"], "x3_y3")
        self.assertTrue(all(item["verified"] is False for item in plan))

    def test_bounded_quadrants_and_snapshot_queries(self):
        self.assertEqual(
            quadrants([139.68, 35.68, 139.70, 35.70])[0], [139.68, 35.68, 139.69, 35.69]
        )
        query = snapshot_query([139.68, 35.68, 139.70, 35.70], "2026-10-21T04:10:31Z")
        self.assertIn('[date:"2026-10-21T04:10:31Z"]', query)
        for stamp in ("now", '2026-10-21";out;', True):
            with self.assertRaises(ValueError):
                snapshot_query([139.68, 35.68, 139.70, 35.70], stamp)

    def test_identity_merge_rejects_version_or_geometry_conflicts(self):
        feature = {
            "type": "node",
            "id": 1,
            "version": 1,
            "lon": 139.69,
            "lat": 35.69,
            "tags": {"natural": "tree"},
        }
        payloads = [{"elements": [copy.deepcopy(feature)]} for _ in range(4)]
        merged = merge_payloads(payloads, "2026-10-21T04:10:31Z")
        self.assertEqual(merged["elements"], [feature])
        payloads[2]["elements"][0]["version"] = 2
        with self.assertRaises(ValueError):
            merge_payloads(payloads, "2026-10-21T04:10:31Z")
        with self.assertRaises(ValueError):
            merge_payloads(payloads[:3], "2026-10-21T04:10:31Z")

    def test_expansion_normalization_and_crown_budgets_remain_finite(self):
        with self.assertRaises(ValueError):
            normalize({"elements": []}, max_features=90001)
        with self.assertRaises(ValueError):
            normalize({"elements": []}, max_coordinates=900001)
        with self.assertRaises(ValueError):
            canopy_samples(
                [], [139.68, 35.68, 139.70, 35.70], [139.69, 35.69], max_canopies=6001
            )

    def test_mixed_snapshots_are_explicit_and_network_charge_is_carried(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            responses = []
            stamps = [f"2026-10-21T22:0{i}:00Z" for i in range(4)]
            for stamp in stamps:
                raw = json.dumps(
                    {"osm3s": {"timestamp_osm_base": stamp}, "elements": []}
                ).encode()
                responses.append((raw, {"fetched_at": stamp}, len(raw)))
            with (
                patch(
                    "fetch_expansion_vegetation.fetch_response", side_effect=responses
                ) as fetch,
                patch(
                    "fetch_expansion_vegetation.write_package",
                    return_value={"features": [], "representation": {}},
                ) as package,
            ):
                acquire(
                    ROOT / "configs/shinjuku-expansion-source.json",
                    directory / "out",
                    directory / "cache",
                    prior_network_charge=44383996,
                    audit_path=directory / "audit.json",
                )
            metadata = package.call_args.args[3]
            self.assertEqual(metadata["snapshot_range"], [stamps[0], stamps[-1]])
            self.assertFalse(metadata["single_instant_snapshot"])
            self.assertEqual(metadata["prior_network_response_body_charge"], 44383996)
            self.assertEqual(
                package.call_args.args[1]["osm3s"],
                {"snapshot_range": [stamps[0], stamps[-1]]},
            )
            self.assertNotIn("timestamp_osm_base", package.call_args.args[1]["osm3s"])
            self.assertTrue(
                all(
                    call.kwargs["max_bytes"] < 25000000 for call in fetch.call_args_list
                )
            )
            self.assertEqual(
                json.loads((directory / "audit.json").read_text())[
                    "charged_response_body_bytes"
                ],
                metadata["total_charged_network_response_body_bytes"],
            )

    def test_primary_vegetation_exclusion_is_recorded_from_official_regions(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "context.json"
            path.write_text(
                json.dumps(
                    {
                        "tiles": [
                            {
                                "context_type": "veg",
                                "bounding_volume": {
                                    "region": [
                                        math.radians(value)
                                        for value in [139.7, 35.69, 139.71, 35.70]
                                    ]
                                    + [0, 100]
                                },
                            }
                        ]
                    }
                )
            )
            result = primary_exclusions(path)
            for value, expected in zip(
                result[0], [139.7, 35.69, 139.71, 35.70], strict=True
            ):
                self.assertAlmostEqual(value, expected)

    def test_building_tagged_site_relation_uses_conservative_source_bounds(self):
        payload = {
            "elements": [
                {
                    "type": "relation",
                    "id": 123,
                    "version": 1,
                    "bounds": {
                        "minlon": 139.7,
                        "minlat": 35.69,
                        "maxlon": 139.71,
                        "maxlat": 35.70,
                    },
                    "tags": {"type": "site", "building": "yes"},
                }
            ]
        }
        features, skipped = normalize(payload)
        self.assertFalse(skipped)
        self.assertEqual(
            features[0]["properties"]["geometry_interpretation"],
            "conservative_building_relation_bounds",
        )
        self.assertEqual(features[0]["geometry"]["coordinates"][0][0], [139.7, 35.69])


if __name__ == "__main__":
    unittest.main()

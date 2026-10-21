"""Native expansion contracts and synthetic identity/review failure checks."""

import gzip
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import art_expansion as expansion
import art_pilot as pilot
import build_map_release as release
import test_build_map_release as fixtures
from atlas import load_config
from render_expansion_source import source_provenance

ROOT = Path(__file__).resolve().parents[1]
ORDER = json.loads((ROOT / "configs/shinjuku-expansion-order.json").read_text())


class ExpansionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.base = Path(cls.temp.name)
        cls.parent_fixture = fixtures.NativeMapReleaseTests()
        cls.parent_fixture.setUp()
        cls.addClassCleanup(cls.parent_fixture.doCleanups)
        cls.parent_fixture.add_repair("old-seam", [1000, 1000, 1048, 1048], "red")
        cls.parent_fixture.build()
        cls.snapshot = "2026-10-22T00:00:00Z"
        cls.expansion_data = (
            cls.parent_fixture.repo / "datasets/shinjuku-expansion-vegetation"
        )
        shutil.copytree(
            cls.parent_fixture.repo / "datasets/shinjuku-vegetation", cls.expansion_data
        )
        (cls.expansion_data / "vegetation.json.gz").write_bytes(
            gzip.compress(
                json.dumps(
                    {"source": {"snapshot_range": [cls.snapshot, cls.snapshot]}}
                ).encode(),
                mtime=0,
            )
        )
        (cls.expansion_data / "SHA256SUMS").write_text(
            "".join(
                f"{release.digest(cls.expansion_data / name)}  {name}\n"
                for name in (
                    "vegetation.json.gz",
                    "NOTICE.txt",
                    "README.txt",
                    "query.overpassql",
                )
            )
        )
        cls.source_provenance = {
            "context_coverage": "Synthetic Shinjuku-only context fixture. Adjacent buildings do not imply adjacent source context coverage.",
            "building_sources": [
                {
                    "label": "Synthetic PLATEAU FY2025 source fixture",
                    "url": "https://www.geospatial.jp/ckan/dataset/plateau-13115-suginami-ku-2025",
                }
            ],
            "vegetation": {
                "snapshot_range": [cls.snapshot, cls.snapshot],
                "public_data_sha256": release.digest(
                    cls.expansion_data / "vegetation.json.gz"
                ),
            },
        }
        cls.directory = cls.base / "complete"
        for name, color, size in (
            ("source", "gray", (5120, 5120)),
            ("structure", "white", (5120, 5120)),
            ("style", "blue", (1254, 1254)),
        ):
            with Image.new("RGB", size, color) as image:
                image.save(cls.base / f"{name}.png")
        expansion.initialize(
            cls.directory,
            cls.base / "source.png",
            cls.base / "structure.png",
            cls.base / "style.png",
            cls.parent_fixture.version(),
            ORDER,
        )
        # Fixture generation is synthetic, not an art model or visual approval.
        for position, ident in enumerate(ORDER):
            geometry = expansion.grid()[ident]
            output = cls.directory / "generation" / f"{ident}.png"
            core = cls.directory / "styled" / f"{ident}.png"
            with Image.new("RGB", (1254, 1254), (120 + position, 70, 160)) as image:
                pilot.clean_png(image, output)
                with image.crop(geometry["crop_box_px"]) as crop:
                    pilot.clean_png(crop, core)
            record = {
                **geometry,
                "styled_sha256": release.digest(core),
                "source_sha256": release.digest(
                    cls.directory / "textured" / f"{ident}.png"
                ),
                "generation_output": {
                    "path": f"generation/{ident}.png",
                    "sha256": release.digest(output),
                },
                "reference_sha256": ["a" * 64],
                "prompt": "Synthetic generation fixture, no geographic accuracy.",
            }
            (cls.directory / "records").mkdir(exist_ok=True)
            pilot.write_json(cls.directory / "records" / f"{ident}.json", record)
        config = json.loads(cls.parent_fixture.config_path.read_text())
        config["bounds_px"] = list(expansion.BOUNDS)
        cls.config = cls.base / "config.json"
        pilot.write_json(cls.config, config)
        cls.receipt = cls.base / "source-receipt.json"
        pilot.write_json(
            cls.receipt,
            {
                "source_provenance": cls.source_provenance,
                "status": "complete",
                "source_config_sha256": load_config(cls.config).fingerprint,
                "native_resolution_px": [5120, 5120],
                "upscaled": False,
                "core_reassembly_pixel_exact": True,
                "outputs": [
                    {
                        "id": ident,
                        "path": name + ".png",
                        "sha256": release.digest(cls.base / (name + ".png")),
                    }
                    for ident, name in (
                        ("source-reference", "source"),
                        ("atlas-ink", "structure"),
                    )
                ],
            },
        )
        cls.prompt = cls.base / "prompt.txt"
        cls.prompt.write_text(
            "Synthetic fixture only, not an image generation request."
        )
        cls.review = expansion.review_template(cls.directory)
        approved = json.loads(json.dumps(cls.review))
        approved["seam_review"]["status"] = "reviewed"
        approved["seam_review"]["note"] = "Synthetic fixture review only."
        for record in (
            approved["seam_review"]["joins"] + approved["seam_review"]["junctions"]
        ):
            record.update(passed=True, note="Synthetic fixture review only.")
        approved["source_comparison"].update(
            status="reviewed", passed=True, note="Synthetic source comparison only."
        )
        pilot.write_json(cls.directory / "review.json", approved)
        expansion.export(
            cls.directory,
            cls.config,
            cls.receipt,
            cls.directory / "review.json",
            cls.prompt,
        )

    def setUp(self):
        self.temp_case = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_case.cleanup)
        self.root = Path(self.temp_case.name)
        self.work = self.root / "expansion"
        shutil.copytree(self.directory, self.work)

    def evidence(self):
        return release.read_json(self.work / "evidence.json")

    def validate(self):
        return release.validate_inputs(
            self.work / "render-index.json", self.config, self.work / "evidence.json"
        )

    def test_signed_global_grid_and_frontier_order(self):
        grid = expansion.grid()
        self.assertEqual(len(grid), 25)
        self.assertEqual(grid["x-1_y-1"]["bounds_px"], [-1024, -1024, 0, 0])
        self.assertEqual(
            expansion.local(grid["x-1_y-1"]["bounds_px"]), [0, 0, 1024, 1024]
        )
        self.assertEqual(grid["x3_y3"]["crop_box_px"], [230, 230, 1254, 1254])
        self.assertEqual(expansion.validate_order(ORDER), ORDER)
        for order in (
            ORDER[:-1],
            [ORDER[0]] * 16,
            ["x-1_y-1"] + [name for name in ORDER if name != "x-1_y-1"],
        ):
            with self.assertRaises(ValueError):
                expansion.validate_order(order)

    def test_exact_parent_art_and_all_56_review_regions(self):
        self.validate()
        self.assertEqual(len(self.review["seam_review"]["joins"]), 40)
        self.assertEqual(len(self.review["seam_review"]["junctions"]), 16)
        records = (
            self.review["seam_review"]["joins"]
            + self.review["seam_review"]["junctions"]
        )
        self.assertEqual(sum(record["passed"] for record in records), 16)
        self.assertEqual(self.review["source_comparison"]["status"], "pending")
        self.assertEqual(self.review["seam_review"]["status"], "pending")
        for ident in pilot.grid():
            self.assertEqual(
                release.digest(self.work / "styled" / f"{ident}.png"),
                release.digest(self.work / "parent/chunks" / ident / "styled.png"),
            )

    def test_parent_native_tampering_cannot_be_hidden_by_plan_hash(self):
        path = self.work / "parent/generation/x0_y0.png"
        with Image.new("RGB", (1280, 1280), "black") as image:
            image.save(path)
        plan = release.read_json(self.work / "plan.json")
        plan["parent_files"]["generation/x0_y0.png"] = release.digest(path)
        pilot.write_json(self.work / "plan.json", plan)
        with self.assertRaisesRegex(ValueError, "immutable inventory"):
            expansion.load_plan(self.work)
        with self.assertRaises(ValueError):
            self.validate()

    def test_parent_identity_and_original_generation_are_mandatory(self):
        original = self.evidence()
        for mutation in ("id", "patch", "repair"):
            evidence = json.loads(json.dumps(original))
            if mutation == "id":
                evidence["parent"]["id"] = "shinjuku-map-" + "0" * 24
            elif mutation == "patch":
                next(
                    record for record in evidence["patches"] if record["id"] == "x0_y0"
                )["reference_sha256"] = ["b" * 64]
            else:
                evidence["repairs"] = []
            pilot.write_json(self.work / "evidence.json", evidence)
            with self.assertRaises(ValueError):
                self.validate()

    def test_prepare_ingest_use_local_pngs_with_global_geometry(self):
        for ident in ORDER:
            for folder in ("records", "styled", "generation"):
                suffix = ".json" if folder == "records" else ".png"
                (self.work / folder / (ident + suffix)).unlink()
        with self.assertRaisesRegex(ValueError, "earlier frontier"):
            expansion.prepare(self.work, ORDER[1])
        reference = expansion.prepare(self.work, ORDER[0])
        self.assertTrue(reference["anchors"])
        self.assertEqual(reference["bounds_px"], [3072, 2048, 4096, 3072])
        with Image.open(self.work / "inputs/x3_y2-context.png") as context:
            self.assertEqual(context.size, (1254, 1254))
        record = expansion.ingest(
            self.work, ORDER[0], self.base / "style.png", self.prompt
        )
        self.assertEqual(record["crop_box_px"], [230, 115, 1254, 1139])
        result = expansion.assemble(self.work, allow_partial=True)
        self.assertEqual(result["assembled_cores"], 10)
        with self.assertRaisesRegex(ValueError, "cannot be overwritten"):
            expansion.ingest(self.work, ORDER[0], self.base / "style.png", self.prompt)
        with self.assertRaisesRegex(ValueError, "cannot be regenerated"):
            expansion.ingest(self.work, "x0_y0", self.base / "style.png", self.prompt)

    def test_source_reference_wrong_size_and_tampering_fail(self):
        (self.work / "records" / (ORDER[-1] + ".json")).unlink()
        (self.work / "styled" / (ORDER[-1] + ".png")).unlink()
        expansion.prepare(self.work, ORDER[-1])
        with Image.new("RGB", (1024, 1024), "red") as image:
            path = self.root / "wrong-size.png"
            image.save(path)
        with self.assertRaisesRegex(ValueError, "no resizing"):
            expansion.ingest(self.work, ORDER[-1], path, self.prompt)
        path = self.work / "inputs" / (ORDER[-1] + "-context.png")
        with Image.new("RGB", (1254, 1254), "black") as image:
            image.save(path)
        reference_path = self.work / "inputs" / (ORDER[-1] + ".json")
        reference = release.read_json(reference_path)
        reference["reference_sha256"][0] = release.digest(path)
        pilot.write_json(reference_path, reference)
        with self.assertRaisesRegex(ValueError, "accepted native neighbours"):
            expansion.ingest(self.work, ORDER[-1], self.base / "style.png", self.prompt)

    def test_review_requires_expanded_regions_and_fresh_source_comparison(self):
        original = self.evidence()
        for mutation in ("missing", "pending", "source", "hash"):
            evidence = json.loads(json.dumps(original))
            if mutation == "missing":
                evidence["seam_review"]["joins"].pop()
            elif mutation == "pending":
                evidence["seam_review"]["status"] = "pending"
            elif mutation == "source":
                evidence["source_comparison"]["passed"] = False
            else:
                evidence["seam_review"]["binding"]["regions_rgb_sha256"]["v0-r-1"] = (
                    "0" * 64
                )
            pilot.write_json(self.work / "evidence.json", evidence)
            with self.assertRaises(ValueError):
                self.validate()

    def test_exact_border_repair_preserves_parent_and_invalidates_changed_reviews(self):
        bounds, window = [-20, 1000, 20, 1048], [-200, 700, 1054, 1954]
        expansion.prepare_repair(self.work, "new-border", bounds, window)
        result = expansion.ingest_repair(
            self.work, "new-border", self.base / "style.png", self.prompt
        )
        self.assertEqual(result["repair_count"], 1)
        review = release.read_json(self.work / "review.json")
        self.assertFalse(review["source_comparison"]["passed"])
        self.assertFalse(
            next(
                record
                for record in review["seam_review"]["joins"]
                if record["id"] == "h1-c0"
            )["passed"]
        )
        with Image.open(self.work / "inspection/atlas.png") as atlas:
            self.assertEqual(atlas.getpixel((1024, 2040)), (0, 0, 255))
        self.assertEqual(
            release.digest(self.work / "parent/chunks/x0_y0/styled.png"),
            release.digest(self.directory / "parent/chunks/x0_y0/styled.png"),
        )
        with self.assertRaisesRegex(ValueError, "immutable"):
            expansion.ingest_repair(
                self.work, "new-border", self.base / "style.png", self.prompt
            )
        # Final export must not reuse the old passing receipt after an exact repair.
        with self.assertRaises(ValueError):
            expansion.export(
                self.work,
                self.config,
                self.receipt,
                self.work / "review.json",
                self.prompt,
            )

    def test_repair_geometry_is_bounded_and_signed(self):
        expansion.repair_geometry(
            "negative", [-1000, -1000, -900, -900], [-1024, -1024, 230, 230]
        )
        for bounds, window in (
            ([-1200, 0, -1100, 100], [-1254, 0, 0, 1254]),
            ([-1024, -1024, 230, 230], [-1024, -1024, 230, 230]),
            ([0, 0, 50, 50], [0, 0, 1255, 1254]),
        ):
            with self.assertRaises(ValueError):
                expansion.repair_geometry("invalid", bounds, window)

    def test_source_producer_context_contract_is_retained_in_public_evidence(self):
        building = self.root / "buildings.json"
        vegetation = self.root / "vegetation.json"
        pilot.write_json(
            building,
            {
                "sources": [
                    {
                        "ward_name": "Shinjuku",
                        "edition": 2025,
                        "required_tiles": 1,
                        "package": {
                            "url": "https://www.geospatial.jp/ckan/dataset/plateau-13104-shinjuku-ku-2025"
                        },
                    }
                ]
            },
        )
        pilot.write_json(
            vegetation, {"source": {"snapshot_range": [self.snapshot, self.snapshot]}}
        )
        actual = source_provenance(building, vegetation, self.expansion_data)
        self.assertEqual(release.validate_source_provenance(actual), actual)
        self.assertIn("selected from Shinjuku only", actual["context_coverage"])
        evidence = self.evidence()
        evidence["source_provenance"] = actual
        pilot.write_json(self.work / "evidence.json", evidence)
        _, _, public, _ = self.validate()
        self.assertEqual(public["source_provenance"], actual)
        for note in (
            "",
            "x" * 1501,
            "/Users/example/private/source",
            "Source https://example.org/context",
            "See www.example.org",
        ):
            with self.assertRaises(ValueError):
                release.validate_source_provenance({**actual, "context_coverage": note})

    def test_new_repairs_cannot_replace_parent_interiors_or_stay_inside_parent(self):
        cases = [
            ([1024, 1024, 2048, 2048], [900, 900, 2154, 2154]),
            ([10, 1000, 100, 1100], [0, 700, 1254, 1954]),
            ([-20, 1000, 257, 1100], [-200, 700, 1054, 1954]),
            ([2815, 1000, 3100, 1100], [2740, 700, 3994, 1954]),
            ([1000, -20, 1100, 257], [700, -200, 1954, 1054]),
            ([1000, 2815, 1100, 3100], [700, 2740, 1954, 3994]),
        ]
        original = self.evidence()
        for box, window in cases:
            with self.subTest(box=box):
                with self.assertRaisesRegex(ValueError, "parent"):
                    expansion.repair_geometry("interior", box, window)
                evidence = json.loads(json.dumps(original))
                evidence["repairs"].append(
                    {"id": "interior", "bounds_px": box, "source_bounds_px": window}
                )
                # This independently exported evidence bypasses the art helper.
                pilot.write_json(self.work / "evidence.json", evidence)
                with self.assertRaisesRegex(ValueError, "parent"):
                    self.validate()

    def test_parent_frontier_exact_protected_boundaries_and_new_only_repairs(self):
        cases = [
            ([-20, 1000, 256, 1100], [-200, 700, 1054, 1954]),
            ([2816, 1000, 3100, 1100], [2740, 700, 3994, 1954]),
            ([1000, -20, 1100, 256], [700, -200, 1954, 1054]),
            ([1000, 2816, 1100, 3100], [700, 2740, 1954, 3994]),
            ([-1000, 1000, 0, 1100], [-1024, 700, 230, 1954]),
            ([3072, 1000, 3200, 1100], [2842, 700, 4096, 1954]),
        ]
        for box, window in cases:
            with self.subTest(box=box):
                self.assertEqual(
                    expansion.repair_geometry("frontier", box, window)["bounds_px"], box
                )
                evidence = self.evidence()
                evidence["repairs"].append(
                    {"id": "frontier", "bounds_px": box, "source_bounds_px": window}
                )
                release.validate_expansion_parent(evidence, self.work)
        # The synthetic parent's historical repair is central and remains valid.
        self.assertEqual(
            self.evidence()["repairs"][0]["bounds_px"], [1000, 1000, 1048, 1048]
        )
        self.validate()

    def test_actual_expanded_database_and_snapshot_are_bound_to_source_receipt(self):
        evidence = self.evidence()
        release.verify_expansion_database(
            self.expansion_data / "vegetation.json.gz", evidence
        )
        wrong = self.root / "wrong.json.gz"
        wrong.write_bytes(
            gzip.compress(
                json.dumps(
                    {
                        "source": {
                            "snapshot_range": [
                                "2026-10-20T00:00:00Z",
                                "2026-10-20T00:00:00Z",
                            ]
                        }
                    }
                ).encode(),
                mtime=0,
            )
        )
        with self.assertRaisesRegex(ValueError, "differs from rendered source"):
            release.verify_expansion_database(wrong, evidence)
        evidence["source_provenance"]["vegetation"]["public_data_sha256"] = (
            release.digest(wrong)
        )
        with self.assertRaisesRegex(ValueError, "actual vegetation database"):
            release.verify_expansion_database(wrong, evidence)
        del evidence["source_provenance"]
        pilot.write_json(self.work / "evidence.json", evidence)
        with self.assertRaisesRegex(ValueError, "evidence schema"):
            self.validate()

    def test_expansion_repair_cap_excludes_but_preserves_parent_history(self):
        (self.work / "repairs").mkdir(exist_ok=True)
        pilot.write_json(
            self.work / "repairs/index.json",
            {
                "schema_version": 1,
                "repairs": [f"repair-{number}" for number in range(9)],
            },
        )
        with self.assertRaisesRegex(ValueError, "at most eight"):
            expansion.repair_records(self.work)
        evidence = self.evidence()
        first = json.loads(json.dumps(evidence["repairs"][0]))
        evidence["repairs"].extend(
            [{**first, "id": f"new-{number}"} for number in range(9)]
        )
        pilot.write_json(self.work / "evidence.json", evidence)
        with self.assertRaisesRegex(ValueError, "at most eight"):
            self.validate()

    def test_expanded_release_is_self_contained_and_legacy_still_verifies(self):
        output = self.root / "public"
        first = release.build_release(
            self.work / "render-index.json",
            self.config,
            self.work / "evidence.json",
            output,
            self.parent_fixture.repo,
        )
        self.assertEqual(first, release.verify_release(output))
        manifest = release.read_json(output / "manifest.json")
        self.assertIn(self.snapshot, manifest["source_edition"])
        self.assertIn(
            "shinjuku-expansion-vegetation", manifest["attribution"][-1]["url"]
        )
        self.assertEqual((manifest["width"], manifest["height"]), (5120, 5120))
        self.assertEqual(
            manifest["coordinate_frame"],
            {"id": "shinjuku-native-v1", "units": "pixels"},
        )
        self.assertEqual(manifest["home_bounds"], [0, 0, 3072, 3072])
        self.assertEqual(manifest["legacy_view_bounds"], [0, 0, 3072, 3072])
        version = output / "releases" / first["id"]
        self.assertEqual(
            (version / "parent/release.json").read_bytes(),
            (self.parent_fixture.version() / "release.json").read_bytes(),
        )
        self.assertFalse((version / "parent/pyramid").exists())
        self.assertEqual(
            release.verify_version(
                self.parent_fixture.version(), self.parent_fixture.version().name
            )["id"],
            self.parent_fixture.version().name,
        )
        (version / "parent/evidence.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "checksum"):
            release.verify_release(output)


if __name__ == "__main__":
    unittest.main()

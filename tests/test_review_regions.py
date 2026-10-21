"""Bounded assembled regions, immutable repairs and explicit review dependencies."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from chunk_pyramid import digest
from jobs import Ledger
from PIL import Image, PngImagePlugin
from review_regions import assemble_region, import_repair, verify_bundle
from review_server import import_index


class ReviewRegionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index_path = self.root / "render-index.json"
        self.database = self.root / "jobs.sqlite"
        self.bounds = [8, 4, 24, 12]
        self.index = {
            "schema_version": 1,
            "status": "complete",
            "ai_styling": False,
            "bounds_px": [0, 0, 32, 16],
            "chunk_size_px": 16,
            "config_sha256": "a" * 64,
            "layers": ["textured", "geometry"],
            "chunks": [],
        }
        for x in (0, 1):
            chunk = {
                "id": f"x{x}_y0",
                "bounds_px": [x * 16, 0, x * 16 + 16, 16],
                "outputs": {},
            }
            for layer in self.index["layers"]:
                path = self.root / f"{layer}-{x}.png"
                image = Image.new("RGBA", (16, 16), (x * 100, 30, 90, 255))
                image.save(path)
                image.close()
                chunk["outputs"][layer] = {
                    "path": path.name,
                    "sha256": digest(path),
                    "width": 16,
                    "height": 16,
                    "bytes": path.stat().st_size,
                }
            self.index["chunks"].append(chunk)
        self.save_index()
        self.jobs = import_index(self.index_path, self.database, self.root)["jobs"]
        self.ledger = Ledger(self.database)
        self.addCleanup(self.ledger.close)
        self.replacement = self.root / "replacement.png"
        info = PngImagePlugin.PngInfo()
        info.add_text("private-path", "/private/example/editor/file")
        image = Image.new("RGBA", (16, 8), (24, 50, 70, 255))
        image.save(self.replacement, pnginfo=info)
        image.close()

    def save_index(self):
        self.index_path.write_text(json.dumps(self.index))

    def assemble(self, bounds=None, layers=None):
        return assemble_region(
            self.index_path,
            self.root / "regions",
            bounds or self.bounds,
            layers or ["textured", "geometry"],
            workspace=self.root,
        )

    def repair(self, region=None, **kwargs):
        region = region or self.assemble()
        options = {
            "bounds": self.bounds,
            "layer": "textured",
            "source_jobs": self.jobs,
            "note": "Repair a seam without changing guide geometry",
        } | kwargs
        return import_repair(
            Path(region["directory"]),
            self.replacement,
            options.pop("bounds"),
            options.pop("layer"),
            options.pop("source_jobs"),
            self.database,
            self.root / "repairs",
            workspace=self.root,
            **options,
        )

    def test_native_pixels_boundaries_and_paired_layers(self):
        result = self.assemble()
        manifest = verify_bundle(Path(result["directory"]))
        self.assertEqual(manifest["covered_pixels"], 128)
        self.assertEqual(manifest["missing_pixels"], 0)
        self.assertEqual(len(manifest["source_chunks"]), 2)
        self.assertEqual(len(manifest["files"]), 5)
        directory = Path(result["directory"])
        with Image.open(directory / "original-textured.png") as image:
            self.assertEqual(image.getpixel((7, 4)), (0, 30, 90, 255))
            self.assertEqual(image.getpixel((8, 4)), (100, 30, 90, 255))
        with Image.open(directory / "boundaries-textured.png") as image:
            self.assertEqual(image.getpixel((8, 4)), (255, 45, 80, 255))
            self.assertEqual(image.getpixel((0, 4)), (0, 30, 90, 255))
        repeat = self.assemble()
        self.assertEqual(result, repeat)

    def test_sparse_bounds_stay_missing_and_cannot_be_repaired(self):
        self.index["chunks"].pop()
        self.save_index()
        result = self.assemble()
        self.assertEqual(result["manifest"]["missing_pixels"], 64)
        with Image.open(Path(result["directory"]) / "coverage.png") as image:
            self.assertEqual(image.getpixel((8, 4)), (0, 0, 0, 255))
        with self.assertRaisesRegex(ValueError, "Sparse"):
            self.repair(result)
        self.assertFalse((self.root / "repairs").exists())

    def test_mismatched_bounds_dimensions_layers_and_limits(self):
        result = self.assemble()
        for bounds in ([9, 4, 25, 12], [8, 4, 24, 13]):
            with self.assertRaisesRegex(ValueError, "bounds"):
                self.repair(result, bounds=bounds)
        with Image.new("RGB", (15, 8)) as image:
            image.save(self.replacement)
        with self.assertRaisesRegex(ValueError, "dimensions"):
            self.repair(result)
        with self.assertRaisesRegex(ValueError, "guides"):
            self.repair(result, layer="geometry")
        with self.assertRaisesRegex(ValueError, "bounded"):
            self.assemble(bounds=[0, 0, 4097, 16])
        with self.assertRaisesRegex(ValueError, "inside"):
            self.assemble(bounds=[-1, 0, 12, 12])
        with self.assertRaisesRegex(ValueError, "distinct"):
            self.assemble(layers=["textured", "textured"])

    def test_source_escape_and_checksum_mismatch(self):
        self.index["chunks"][0]["outputs"]["textured"]["path"] = "../escape.png"
        self.save_index()
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.assemble()
        self.index["chunks"][0]["outputs"]["textured"]["path"] = "textured-0.png"
        self.save_index()
        (self.root / "textured-0.png").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.assemble()

    def test_metadata_stripped_and_originals_guides_unchanged(self):
        before = {p.name: p.read_bytes() for p in self.root.glob("*-?.png")}
        result = self.repair()
        self.assertEqual(result["state"], "needs_review")
        manifest = verify_bundle(Path(result["directory"]), "manual-repair")
        self.assertFalse(manifest["guides_modified"])
        self.assertEqual(manifest["replacement_input_sha256"], digest(self.replacement))
        with Image.open(Path(result["directory"]) / "repaired.png") as image:
            self.assertEqual(image.info, {})
            self.assertEqual(image.getpixel((3, 4)), (24, 50, 70, 255))
        self.assertNotIn(
            "/private/example",
            (Path(result["directory"]) / "manifest.json").read_text(),
        )
        self.assertEqual(
            before, {p.name: p.read_bytes() for p in self.root.glob("*-?.png")}
        )
        deps = self.ledger.db.execute(
            "SELECT dependency FROM dependencies WHERE job=?", (result["job_id"],)
        ).fetchall()
        self.assertEqual({row["dependency"] for row in deps}, set(self.jobs))
        with self.assertRaisesRegex(ValueError, "depend"):
            self.ledger.approve(result["job_id"], self.root, "Not ready")

    def test_repair_invalidates_only_descendants_and_retry_is_idempotent(self):
        for ident in self.jobs:
            self.ledger.approve(ident, self.root, "Fixture source checked")
        child = self.ledger.enqueue("style", "region", {}, self.jobs)
        grandchild = self.ledger.enqueue("pyramid", "region", {}, [child])
        unrelated = self.ledger.enqueue("other", "region", {})
        running = self.ledger.claim("worker", job_ids=[child])
        result = self.repair()
        for ident in (child, grandchild):
            self.assertEqual(self.ledger.get(ident)["state"], "failed")
        self.assertEqual(self.ledger.get(unrelated)["state"], "pending")
        for ident in self.jobs:
            self.assertEqual(self.ledger.get(ident)["state"], "approved")
        with self.assertRaisesRegex(ValueError, "stale"):
            self.ledger.finish(
                child, running["lease_token"], self.root, ["replacement.png"]
            )
        events = self.ledger.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertEqual(result, self.repair())
        self.assertEqual(
            events, self.ledger.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        )
        self.ledger.approve(
            result["job_id"], self.root, "Candidate independently reviewed"
        )
        self.assertEqual(self.repair()["state"], "approved")

    def test_new_candidate_invalidates_previous_repair_but_not_source(self):
        first = self.repair()
        with Image.new("RGBA", (16, 8), (100, 30, 40, 255)) as image:
            image.save(self.replacement)
        second = self.repair()
        self.assertNotEqual(first["repair_id"], second["repair_id"])
        self.assertEqual(self.ledger.get(first["job_id"])["state"], "failed")
        self.assertEqual(self.ledger.get(second["job_id"])["state"], "needs_review")
        self.assertTrue(Path(first["directory"]).is_dir())

    def test_missing_foreign_and_mutated_source_jobs_are_rejected(self):
        region = self.assemble()
        with self.assertRaisesRegex(ValueError, "Every"):
            self.repair(region, source_jobs=self.jobs[:1])
        with self.assertRaisesRegex(ValueError, "Unique"):
            self.repair(region, source_jobs=self.jobs + self.jobs)
        foreign = self.ledger.import_completed(
            "foreign", "x", {}, self.root, ["replacement.png"]
        )
        with self.assertRaisesRegex(ValueError, "match"):
            self.repair(region, source_jobs=[foreign])
        (self.root / "geometry-0.png").write_bytes(b"mutated other source artifact")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.repair(region)

    def test_tampered_bundle_and_symlink_escape_are_rejected(self):
        result = self.assemble()
        path = Path(result["directory"]) / "original-textured.png"
        path.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "checksum"):
            verify_bundle(Path(result["directory"]))
        link = self.root / "link"
        link.symlink_to(Path(result["directory"]), target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            verify_bundle(link)
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.assemble()

    def test_edit_method_is_explicit_provenance_in_manifest_and_job(self):
        result = self.repair(edit_method="ai-assisted")
        manifest = verify_bundle(Path(result["directory"]), "manual-repair")
        self.assertEqual(manifest["edit_method"], "ai-assisted")
        self.assertEqual(
            self.ledger.get(result["job_id"])["payload"]["edit_method"], "ai-assisted"
        )
        with self.assertRaisesRegex(ValueError, "edit method"):
            self.repair(edit_method="unknown")

    def test_source_mutation_during_assembly_is_rejected(self):
        import review_regions

        original = review_regions.clean_png
        calls = []

        def mutate(image, path):
            record = original(image, path)
            calls.append(path)
            if len(calls) == 1:
                asset = self.root / "textured-0.png"
                with Image.new("RGBA", (16, 16), (70, 80, 90, 255)) as altered:
                    altered.save(asset)
            return record

        with patch("review_regions.clean_png", side_effect=mutate):
            with self.assertRaisesRegex(ValueError, "changed during assembly"):
                self.assemble()
        self.assertEqual(list((self.root / "regions").iterdir()), [])

    def test_nonmatching_index_provenance_is_rejected(self):
        region = self.assemble()
        job = self.ledger.get(self.jobs[0])
        bad = self.ledger.import_completed(
            "source-render",
            "fixture",
            job["payload"] | {"render_index_sha256": "b" * 64},
            self.root,
            [record["path"] for record in job["artifacts"]],
        )
        with self.assertRaisesRegex(ValueError, "match"):
            self.repair(region, source_jobs=[bad, self.jobs[1]])

    def test_crash_before_ledger_registration_retries_immutable_bundle(self):
        with patch(
            "review_regions.Ledger.import_completed",
            side_effect=RuntimeError("interrupted"),
        ):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self.repair()
        directories = list((self.root / "repairs").iterdir())
        self.assertEqual(len(directories), 1)
        verify_bundle(directories[0], "manual-repair")
        result = self.repair()
        self.assertEqual(Path(result["directory"]), directories[0].resolve())
        self.assertEqual(result["state"], "needs_review")


if __name__ == "__main__":
    unittest.main()

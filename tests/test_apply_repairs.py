"""Reviewed exact repair splices, immutable release identity and pyramid reuse."""

import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import test_review_regions as fixture
from apply_repairs import apply_repairs, verify_release
from chunk_pyramid import build_pyramid, digest
from PIL import Image
from review_regions import content_id, write_json
from review_server import import_index


class ApplyRepairsTests(unittest.TestCase):
    setUp = fixture.ReviewRegionTests.setUp
    save_index = fixture.ReviewRegionTests.save_index
    assemble = fixture.ReviewRegionTests.assemble
    repair = fixture.ReviewRegionTests.repair

    def approved(self, **kwargs):
        for ident in self.jobs:
            if self.ledger.get(ident)["state"] == "needs_review":
                self.ledger.approve(ident, self.root, "Fixture sources reviewed")
        result = self.repair(**kwargs)
        self.ledger.approve(
            result["job_id"], self.root, "Synthetic repair reviewed for test"
        )
        return result

    def apply(self, identifiers, **kwargs):
        return apply_repairs(
            self.index_path,
            self.database,
            identifiers,
            kwargs.pop("output", self.root / "releases"),
            workspace=self.root,
            **kwargs,
        )

    def restore_approval(self, ident):
        job = self.ledger.get(ident)
        self.ledger.retry(ident)
        running = self.ledger.claim("test", job_ids=[ident])
        self.ledger.finish(
            ident,
            running["lease_token"],
            self.root,
            [r["path"] for r in job["artifacts"]],
        )
        self.ledger.approve(ident, self.root, "Explicit synthetic re-review")

    def add_guides(self):
        for x, chunk in enumerate(self.index["chunks"]):
            path = self.root / f"guide-{x}.png"
            with Image.new("L", (16, 16), x + 1) as image:
                image.save(path)
            chunk["guides"] = {
                "semantic": {
                    "path": path.name,
                    "width": 16,
                    "height": 16,
                    "bytes": path.stat().st_size,
                    "sha256": digest(path),
                }
            }
        self.save_index()
        self.jobs = import_index(self.index_path, self.database, self.root)["jobs"]

    def test_exact_splice_keeps_other_pixels_layers_and_guides_unchanged(self):
        self.add_guides()
        repair = self.approved()
        original = {path.name: path.read_bytes() for path in self.root.glob("*.png")}
        result = self.apply([repair["job_id"]])
        output = Path(result["directory"])
        index = verify_release(output)
        self.assertFalse(index["artwork_approved"])
        self.assertFalse(index["ai_styling"])
        self.assertFalse(index["source_coverage_verified"])
        self.assertFalse(index["guides_modified"])
        self.assertNotIn("reference", index)
        self.assertEqual(index["source_index_sha256"], digest(self.index_path))
        for x, chunk in enumerate(index["chunks"]):
            self.assertEqual(
                (output / chunk["outputs"]["geometry"]["path"]).read_bytes(),
                original[f"geometry-{x}.png"],
            )
            self.assertEqual(
                (output / chunk["guides"]["semantic"]["path"]).read_bytes(),
                original[f"guide-{x}.png"],
            )
            with Image.open(output / chunk["outputs"]["textured"]["path"]) as image:
                for y in range(16):
                    for xx in range(16):
                        global_x = x * 16 + xx
                        expected = (
                            (24, 50, 70, 255)
                            if 8 <= global_x < 24 and 4 <= y < 12
                            else (x * 100, 30, 90, 255)
                        )
                        self.assertEqual(image.getpixel((xx, y)), expected)
        self.assertEqual(
            original, {path.name: path.read_bytes() for path in self.root.glob("*.png")}
        )
        self.assertEqual(result, self.apply([repair["job_id"]]))

    def test_unapproved_failed_and_nonrepair_jobs_are_rejected(self):
        repair = self.repair()
        with self.assertRaisesRegex(ValueError, "approved"):
            self.apply([repair["job_id"]])
        for ident in self.jobs:
            self.ledger.approve(ident, self.root, "Synthetic source reviewed")
        with self.assertRaisesRegex(ValueError, "manual-repair"):
            self.apply([self.jobs[0]])
        self.ledger.approve(repair["job_id"], self.root, "Synthetic candidate reviewed")
        self.ledger.invalidate(self.jobs[0], "Synthetic source correction")
        with self.assertRaisesRegex(ValueError, "approved"):
            self.apply([repair["job_id"]])

    def test_transitive_dependency_artifacts_are_rechecked(self):
        repair = self.approved()
        path = self.root / "ancestor.txt"
        path.write_text("before")
        ancestor = self.ledger.import_completed(
            "source", "ancestor", {}, self.root, [path.name]
        )
        self.ledger.approve(ancestor, self.root, "Synthetic input checked")
        self.ledger.db.execute(
            "INSERT INTO dependencies VALUES(?,?)", (self.jobs[0], ancestor)
        )
        path.write_text("after")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.apply([repair["job_id"]])
        self.assertFalse((self.root / "releases").exists())

    def test_changed_source_index_and_repair_bytes_are_rejected(self):
        repair = self.approved()
        self.index["extra"] = "Changed index provenance"
        self.save_index()
        with self.assertRaisesRegex(ValueError, "match"):
            self.apply([repair["job_id"]])
        del self.index["extra"]
        self.save_index()
        (Path(repair["directory"]) / "repaired.png").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.apply([repair["job_id"]])

    def test_overlapping_repairs_and_duplicate_ids_are_rejected(self):
        first = self.approved()
        with Image.new("RGBA", (16, 8), (10, 20, 30, 255)) as image:
            image.save(self.replacement)
        second = self.approved()
        self.restore_approval(first["job_id"])
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            self.apply([first["job_id"], second["job_id"]])
        with self.assertRaisesRegex(ValueError, "unique"):
            self.apply([first["job_id"], first["job_id"]])

    def test_transparent_repair_replaces_instead_of_alpha_blending(self):
        with Image.new("RGBA", (16, 8), (24, 50, 70, 0)) as image:
            image.save(self.replacement)
        repair = self.approved()
        result = self.apply([repair["job_id"]])
        index = verify_release(Path(result["directory"]))
        with Image.open(
            Path(result["directory"])
            / index["chunks"][0]["outputs"]["textured"]["path"]
        ) as image:
            self.assertEqual(image.getpixel((10, 5)), (24, 50, 70, 0))

    def test_incremental_pyramid_matches_full_and_reuses_other_layer(self):
        repair = self.approved()
        result = self.apply([repair["job_id"]])
        incremental = self.root / "pyramid"
        build_pyramid(self.index_path, incremental, tile_size=16)
        geometry_before = {
            p.relative_to(incremental).as_posix(): p.read_bytes()
            for p in incremental.glob("geometry_files/**/*.webp")
        }
        updated = build_pyramid(Path(result["index"]), incremental, tile_size=16)
        self.assertGreater(updated["reused_tiles"], 0)
        self.assertGreater(updated["generated_tiles"], 0)
        full = self.root / "full-pyramid"
        build_pyramid(Path(result["index"]), full, tile_size=16, incremental=False)
        self.assertEqual(
            {
                p.relative_to(incremental).as_posix(): p.read_bytes()
                for p in incremental.rglob("*.webp")
            },
            {
                p.relative_to(full).as_posix(): p.read_bytes()
                for p in full.rglob("*.webp")
            },
        )
        self.assertEqual(
            geometry_before,
            {
                p.relative_to(incremental).as_posix(): p.read_bytes()
                for p in incremental.glob("geometry_files/**/*.webp")
            },
        )

    def test_tampered_immutable_release_is_not_replaced(self):
        repair = self.approved()
        result = self.apply([repair["job_id"]])
        directory = Path(result["directory"])
        index = verify_release(directory)
        asset = directory / index["chunks"][0]["outputs"]["textured"]["path"]
        asset.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "checksum"):
            verify_release(directory)
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.apply([repair["job_id"]])
        self.assertEqual(asset.read_bytes(), b"tampered")

    def test_midbuild_source_mutation_does_not_publish(self):
        import apply_repairs as implementation

        repair = self.approved()
        original = implementation.clean_png

        def alter(image, path):
            record = original(image, path)
            (self.root / "textured-0.png").write_bytes(b"altered source")
            return record

        with patch("apply_repairs.clean_png", side_effect=alter):
            with self.assertRaises(ValueError):
                self.apply([repair["job_id"]])
        self.assertEqual(list((self.root / "releases").iterdir()), [])

    def test_numeric_guides_and_registry_are_byte_identical(self):
        self.add_guides()
        for x, chunk in enumerate(self.index["chunks"]):
            path = self.root / f"depth-{x}.f32"
            path.write_bytes(b"\0" * 16 * 16 * 4)
            chunk["guides"]["depth"] = {
                "path": path.name,
                "sha256": digest(path),
                "bytes": path.stat().st_size,
                "width": 16,
                "height": 16,
                "dtype": "float32-little-endian",
                "components": 1,
            }
        registry = self.root / "registry.json"
        registry.write_text('{"records": []}\n')
        self.index["guide_metadata"] = {
            "feature_id": {
                "registry": registry.name,
                "registry_sha256": digest(registry),
            }
        }
        self.save_index()
        self.jobs = import_index(self.index_path, self.database, self.root)["jobs"]
        repair = self.approved()
        result = self.apply([repair["job_id"]])
        directory = Path(result["directory"])
        index = verify_release(directory)
        self.assertEqual(
            (
                directory / index["guide_metadata"]["feature_id"]["registry"]
            ).read_bytes(),
            registry.read_bytes(),
        )
        for x, chunk in enumerate(index["chunks"]):
            self.assertEqual(
                (directory / chunk["guides"]["depth"]["path"]).read_bytes(),
                (self.root / f"depth-{x}.f32").read_bytes(),
            )

    def test_adjacent_repairs_are_order_independent(self):
        first_region = self.assemble(bounds=[0, 0, 16, 8])
        first = self.approved(
            region=first_region, bounds=[0, 0, 16, 8], source_jobs=[self.jobs[0]]
        )
        second_region = self.assemble(bounds=[16, 0, 32, 8])
        with Image.new("RGBA", (16, 8), (110, 120, 130, 255)) as image:
            image.save(self.replacement)
        second = self.approved(
            region=second_region, bounds=[16, 0, 32, 8], source_jobs=[self.jobs[1]]
        )
        identifiers = [first["job_id"], second["job_id"]]
        self.assertEqual(
            self.apply(identifiers), self.apply(list(reversed(identifiers)))
        )

    def test_forged_preserved_source_pixels_fail_even_with_approved_file_hashes(self):
        repair = self.approved()
        old = Path(repair["directory"])
        temporary = self.root / "forged-stage"
        shutil.copytree(old, temporary)
        manifest = json.loads((temporary / "manifest.json").read_text())
        with Image.new("RGBA", (16, 8), (250, 250, 250, 255)) as image:
            image.save(temporary / "source.png")
        record = manifest["files"]["source"]
        record.update(
            sha256=digest(temporary / "source.png"),
            bytes=(temporary / "source.png").stat().st_size,
        )
        del manifest["id"]
        manifest["id"] = content_id(manifest)
        write_json(temporary / "manifest.json", manifest)
        destination = self.root / manifest["id"]
        temporary.rename(destination)
        payload = self.ledger.get(repair["job_id"])["payload"] | {
            "repair_id": manifest["id"]
        }
        paths = [
            path.relative_to(self.root).as_posix()
            for path in sorted(destination.iterdir())
        ]
        ident = self.ledger.import_completed(
            "manual-repair",
            "synthetic",
            payload,
            self.root,
            paths,
            dependencies=self.jobs,
        )
        self.ledger.approve(ident, self.root, "Synthetic corrupted-preservation test")
        with self.assertRaisesRegex(ValueError, "preserved source pixels"):
            self.apply([ident])

    def test_ai_assisted_repair_sets_style_provenance_true(self):
        repair = self.approved(edit_method="ai-assisted")
        result = self.apply([repair["job_id"]])
        self.assertTrue(verify_release(Path(result["directory"]))["ai_styling"])

    def test_manual_repair_keeps_existing_ai_style_provenance(self):
        self.index["ai_styling"] = True
        self.save_index()
        self.jobs = import_index(self.index_path, self.database, self.root)["jobs"]
        repair = self.approved(edit_method="manual")
        result = self.apply([repair["job_id"]])
        self.assertTrue(verify_release(Path(result["directory"]))["ai_styling"])

    def test_unknown_source_style_provenance_is_rejected(self):
        repair = self.approved()
        for value in (None, "false", 0):
            self.index["ai_styling"] = value
            self.save_index()
            with self.assertRaisesRegex(ValueError, "ai_styling provenance"):
                self.apply([repair["job_id"]])
        del self.index["ai_styling"]
        self.save_index()
        with self.assertRaisesRegex(ValueError, "ai_styling provenance"):
            self.apply([repair["job_id"]])

    def test_output_cannot_modify_immutable_repair_bundle(self):
        repair = self.approved()
        forbidden = Path(repair["directory"]) / "release-output"
        with self.assertRaisesRegex(ValueError, "immutable source bundle"):
            self.apply([repair["job_id"]], output=forbidden)
        self.assertFalse(forbidden.exists())

    def test_guide_escape_and_study_limits(self):
        self.add_guides()
        repair = self.approved()
        self.index["chunks"][0]["guides"]["semantic"]["path"] = "../escape.png"
        self.save_index()
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.apply([repair["job_id"]])
        self.index["chunks"][0]["guides"]["semantic"]["path"] = "guide-0.png"
        self.save_index()
        with patch("apply_repairs.MAX_CHUNKS", 1):
            with self.assertRaisesRegex(ValueError, "chunk count"):
                self.apply([repair["job_id"]])
        with patch("apply_repairs.MAX_BYTES", 100):
            with self.assertRaisesRegex(ValueError, "byte limit"):
                self.apply([repair["job_id"]])


if __name__ == "__main__":
    unittest.main()

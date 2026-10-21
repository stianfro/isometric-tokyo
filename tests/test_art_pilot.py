"""Native image identity and context guards, not automated art approval."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from art_pilot import (  # noqa: E402
    CORE,
    ORDER,
    SIZE,
    WINDOW,
    assemble,
    digest,
    export,
    grid,
    ingest,
    ingest_repair,
    initialize,
    prepare,
    prepare_repair,
    repair_records,
    review_template,
)


class ArtPilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / "pilot"
        self.source = self.root / "source.png"
        self.style = self.root / "style.png"
        Image.new("RGB", (SIZE, SIZE), (30, 60, 90)).save(self.source)
        Image.new("RGB", (WINDOW, WINDOW), (90, 70, 50)).save(self.style)
        self.prompt = self.root / "prompt.txt"
        self.prompt.write_text("Test fixture, not generated artwork.")
        initialize(self.directory, self.source, self.source, self.style)

    def accept(self, identifier, color=(90, 70, 50)):
        prepare(self.directory, identifier)
        generated = self.root / "output.png"
        Image.new("RGB", (WINDOW, WINDOW), color).save(generated)
        return ingest(self.directory, identifier, generated, self.prompt)

    def complete(self):
        for number, identifier in enumerate(ORDER):
            self.accept(identifier, (number * 20, 100, 200))
        assemble(self.directory)

    def approve_fixture(self):
        review = review_template(self.directory)
        review["seam_review"].update(status="reviewed", note="Synthetic fixture only")
        for kind in ("joins", "junctions"):
            for item in review["seam_review"][kind]:
                item.update(passed=True, note="Synthetic fixture only")
        review["source_comparison"].update(
            status="reviewed", passed=True, note="Synthetic fixture only"
        )
        path = self.directory / "review.json"
        path.write_text(json.dumps(review))
        return path

    def source_evidence(self):
        from atlas import load_config

        config = (
            Path(__file__).resolve().parents[1] / "configs/shinjuku-pilot-source.json"
        )
        path = self.root / "pilot-source.json"
        path.write_text(
            json.dumps(
                {
                    "status": "complete",
                    "source_config_sha256": load_config(config).fingerprint,
                    "native_resolution_px": [SIZE, SIZE],
                    "upscaled": False,
                    "core_reassembly_pixel_exact": True,
                    "outputs": [
                        {
                            "id": name,
                            "path": self.source.name,
                            "sha256": digest(self.source),
                        }
                        for name in ("source-reference", "atlas-ink")
                    ],
                }
            )
        )
        return config, path

    def repair_image(self, color="red"):
        path = self.root / "repair.png"
        Image.new("RGB", (WINDOW, WINDOW), color).save(path)
        return path

    def test_grid_has_exact_native_partition_and_bounded_context(self):
        cells = grid()
        self.assertEqual(len(cells), 9)
        for patch in cells.values():
            x, y, right, bottom = patch["bounds_px"]
            left, top, far, low = patch["source_bounds_px"]
            crop = patch["crop_box_px"]
            self.assertEqual((right - x, bottom - y), (CORE, CORE))
            self.assertEqual((far - left, low - top), (WINDOW, WINDOW))
            self.assertTrue(0 <= left < far <= SIZE and 0 <= top < low <= SIZE)
            self.assertEqual(crop, [x - left, y - top, right - left, bottom - top])

    def test_context_preserves_accepted_neighbor_pixels(self):
        self.accept("x1_y1", (200, 100, 20))
        reference = prepare(self.directory, "x1_y0")
        self.assertEqual([anchor["id"] for anchor in reference["anchors"]], ["x1_y1"])
        with Image.open(self.directory / "inputs/x1_y0-context.png") as image:
            self.assertEqual(image.getpixel((200, 1100)), (200, 100, 20))
            self.assertEqual(image.getpixel((200, 100)), (30, 60, 90))

    def test_generation_must_follow_order(self):
        with self.assertRaisesRegex(ValueError, "earlier cores"):
            prepare(self.directory, "x0_y0")

    def test_wrong_native_dimensions_are_rejected_without_resizing(self):
        prepare(self.directory, ORDER[0])
        with self.assertRaisesRegex(ValueError, "no resizing"):
            ingest(self.directory, ORDER[0], self.source, self.prompt)
        self.assertFalse((self.directory / "records/x1_y1.json").exists())

    def test_accepted_outputs_are_immutable(self):
        self.accept(ORDER[0])
        with self.assertRaisesRegex(ValueError, "immutable"):
            prepare(self.directory, ORDER[0])
        with self.assertRaisesRegex(ValueError, "cannot be overwritten"):
            ingest(self.directory, ORDER[0], self.style, self.prompt)

    def test_source_identity_cannot_change(self):
        Image.new("RGB", (SIZE, SIZE), "red").save(self.directory / "inputs/source.png")
        with self.assertRaisesRegex(ValueError, "reference changed"):
            prepare(self.directory, ORDER[0])

    def test_prepared_context_identity_cannot_change(self):
        prepare(self.directory, ORDER[0])
        Image.new("RGB", (WINDOW, WINDOW), "red").save(
            self.directory / "inputs/x1_y1-context.png"
        )
        with self.assertRaisesRegex(ValueError, "reference changed"):
            ingest(self.directory, ORDER[0], self.style, self.prompt)

    def test_prepared_grid_cannot_change(self):
        prepare(self.directory, ORDER[0])
        path = self.directory / "inputs/x1_y1.json"
        reference = json.loads(path.read_text())
        reference["crop_box_px"] = [0, 0, 1024, 1024]
        path.write_text(json.dumps(reference))
        with self.assertRaisesRegex(ValueError, "fixed source grid"):
            ingest(self.directory, ORDER[0], self.style, self.prompt)

    def test_accepted_context_tampering_is_rejected(self):
        self.accept(ORDER[0])
        Image.new("RGB", (CORE, CORE), "red").save(self.directory / "styled/x1_y1.png")
        with self.assertRaisesRegex(ValueError, "context pixels changed"):
            prepare(self.directory, ORDER[1])

    def test_exact_assembly_and_all_internal_review_regions(self):
        for number, identifier in enumerate(ORDER):
            self.accept(identifier, (number * 20, 100, 200))
        result = assemble(self.directory)
        self.assertEqual(
            result, {"assembled_cores": 9, "total_cores": 9, "inspection_regions": 16}
        )
        with Image.open(self.directory / "inspection/atlas.png") as image:
            self.assertEqual(image.size, (SIZE, SIZE))
            self.assertEqual(image.info, {})
            for number, identifier in enumerate(ORDER):
                x, y = grid()[identifier]["bounds_px"][:2]
                self.assertEqual(
                    image.getpixel((x + 200, y + 200)), (number * 20, 100, 200)
                )
        record = json.loads((self.directory / "records/x1_y1.json").read_text())
        self.assertFalse(record["geographic_fidelity_approved"])
        self.assertIsNone(record["generation_cost_reported"])
        pending = review_template(self.directory)
        self.assertEqual(pending["seam_review"]["status"], "pending")
        self.assertEqual(
            len(pending["seam_review"]["binding"]["regions_rgb_sha256"]), 16
        )
        self.assertFalse(
            any(item["passed"] for item in pending["seam_review"]["joins"])
        )
        self.assertFalse(pending["source_comparison"]["passed"])

    def test_partial_assembly_cannot_be_confused_with_complete(self):
        self.accept(ORDER[0])
        self.assertEqual(assemble(self.directory, True)["assembled_cores"], 1)
        with self.assertRaises(FileNotFoundError):
            assemble(self.directory)

    def test_exact_ordered_repairs_preserve_originals_and_require_new_review(self):
        from build_map_release import validate_inputs

        self.complete()
        original_files = {
            path: path.read_bytes()
            for folder in ("records", "generation", "textured")
            for path in (self.directory / folder).iterdir()
        }
        old_review = self.approve_fixture()
        old_review_bytes = old_review.read_bytes()
        with Image.open(self.directory / "inspection/atlas.png") as image:
            expected = image.copy()
        bounds, window = [1000, 1000, 1048, 1048], [400, 400, 1654, 1654]
        reference = prepare_repair(self.directory, "first", bounds, window)
        self.assertEqual(reference["crop_box_px"], [600, 600, 648, 648])
        result = ingest_repair(
            self.directory, "first", self.repair_image(), self.prompt
        )
        self.assertEqual(result["review_status"], "pending")
        expected.paste("red", tuple(bounds))
        with Image.open(self.directory / "inspection/atlas.png") as actual:
            self.assertEqual(expected.tobytes(), actual.tobytes())
        self.assertEqual(
            (self.directory / "repairs/first-prior-review.json").read_bytes(),
            old_review_bytes,
        )
        reset = json.loads(old_review.read_text())
        self.assertEqual(reset["seam_review"]["status"], "pending")
        self.assertFalse(reset["source_comparison"]["passed"])
        self.assertFalse(any(item["passed"] for item in reset["seam_review"]["joins"]))
        config, metadata = self.source_evidence()
        with self.assertRaisesRegex(ValueError, "completed visual seam review"):
            export(self.directory, config, metadata, old_review, self.prompt)
        stale = self.root / "stale-review.json"
        stale.write_bytes(old_review_bytes)
        with self.assertRaisesRegex(ValueError, "different artwork"):
            export(self.directory, config, metadata, stale, self.prompt)
        second = [1010, 1010, 1030, 1030]
        prepare_repair(self.directory, "second", second, window)
        ingest_repair(self.directory, "second", self.repair_image("blue"), self.prompt)
        expected.paste("blue", tuple(second))
        with Image.open(self.directory / "inspection/atlas.png") as actual:
            self.assertEqual(expected.tobytes(), actual.tobytes())
        expected.close()
        for path, content in original_files.items():
            self.assertEqual(path.read_bytes(), content)
        export(self.directory, config, metadata, self.approve_fixture(), self.prompt)
        evidence = json.loads((self.directory / "evidence.json").read_text())
        self.assertEqual(
            [item["id"] for item in evidence["repairs"]], ["first", "second"]
        )
        validate_inputs(
            self.directory / "render-index.json",
            config,
            self.directory / "evidence.json",
        )
        self.assertEqual(len(repair_records(self.directory)), 2)

    def test_repair_rejects_wrong_size_changed_references_and_invalid_bounds(self):
        self.complete()
        window = [400, 400, 1654, 1654]
        for bounds in ([1000, 1000, 1700, 1048], [1000, 1000, 1000, 1048]):
            with self.assertRaises(ValueError):
                prepare_repair(self.directory, "invalid", bounds, window)
        with self.assertRaisesRegex(ValueError, "native 1254"):
            prepare_repair(
                self.directory, "invalid", [1000, 1000, 1048, 1048], [0, 0, 1255, 1254]
            )
        prepare_repair(self.directory, "guarded", [1000, 1000, 1048, 1048], window)
        with self.assertRaisesRegex(ValueError, "no resizing"):
            ingest_repair(self.directory, "guarded", self.source, self.prompt)
        self.assertFalse((self.directory / "repairs/index.json").exists())
        context = self.directory / "inputs/repair-guarded-context.png"
        Image.new("RGB", (WINDOW, WINDOW), "red").save(context)
        with self.assertRaisesRegex(ValueError, "reference changed"):
            ingest_repair(self.directory, "guarded", self.repair_image(), self.prompt)
        self.assertFalse((self.directory / "generation/repair-guarded.png").exists())

    def test_stale_repair_context_and_immutable_repair_ids_are_rejected(self):
        self.complete()
        bounds, window = [1000, 1000, 1048, 1048], [400, 400, 1654, 1654]
        prepare_repair(self.directory, "first", bounds, window)
        prepare_repair(self.directory, "stale", bounds, window)
        image = self.repair_image()
        ingest_repair(self.directory, "first", image, self.prompt)
        with self.assertRaisesRegex(ValueError, "different artwork"):
            ingest_repair(self.directory, "stale", image, self.prompt)
        with self.assertRaisesRegex(ValueError, "immutable"):
            prepare_repair(self.directory, "first", bounds, window)
        with self.assertRaisesRegex(ValueError, "cannot be overwritten"):
            ingest_repair(self.directory, "first", image, self.prompt)
        repaired = self.directory / "styled/x0_y0.png"
        Image.new("RGB", (CORE, CORE), "black").save(repaired)
        with self.assertRaisesRegex(ValueError, "outside recorded native repairs"):
            assemble(self.directory)

    def test_at_most_four_native_repair_imports_are_allowed(self):
        self.complete()
        bounds, window = [1000, 1000, 1048, 1048], [400, 400, 1654, 1654]
        for number, color in enumerate(("red", "blue", "green", "yellow")):
            identifier = f"repair-{number}"
            prepare_repair(self.directory, identifier, bounds, window)
            ingest_repair(
                self.directory, identifier, self.repair_image(color), self.prompt
            )
        with self.assertRaisesRegex(ValueError, "At most four"):
            prepare_repair(self.directory, "fifth", bounds, window)
        with self.assertRaisesRegex(ValueError, "At most four"):
            ingest_repair(self.directory, "fifth", self.repair_image(), self.prompt)

    def test_export_requires_native_source_identity_and_complete_visual_review(self):
        from atlas import load_config
        from build_map_release import JOINS, JUNCTIONS, review_binding, validate_inputs

        config = (
            Path(__file__).resolve().parents[1] / "configs/shinjuku-pilot-source.json"
        )
        for number, identifier in enumerate(ORDER):
            self.accept(identifier, (number * 20, 100, 200))
        source_metadata = self.root / "pilot-source.json"
        source = {
            "status": "complete",
            "source_config_sha256": load_config(config).fingerprint,
            "native_resolution_px": [SIZE, SIZE],
            "upscaled": False,
            "core_reassembly_pixel_exact": True,
            "outputs": [
                {"id": name, "path": self.source.name, "sha256": digest(self.source)}
                for name in ("source-reference", "atlas-ink")
            ],
        }
        source_metadata.write_text(json.dumps(source))
        review_path = self.root / "review.json"
        review = {
            "status": "reviewed",
            "note": "Synthetic fixture, no artwork approval",
            "joins": [
                {"id": name, "passed": True, "note": "Test fixture only"}
                for name in sorted(JOINS)
            ],
            "junctions": [
                {"id": name, "passed": True, "note": "Test fixture only"}
                for name in sorted(JUNCTIONS)
            ],
        }
        assemble(self.directory)
        with (
            Image.open(self.directory / "inspection/atlas.png") as atlas,
            Image.open(self.source) as source_image,
        ):
            review["binding"] = review_binding(atlas, source_image)
        review = {
            "seam_review": review,
            "source_comparison": {
                "status": "reviewed",
                "passed": True,
                "note": "Synthetic fixture only",
            },
        }
        review_path.write_text(json.dumps(review))
        result = export(
            self.directory, config, source_metadata, review_path, self.prompt
        )
        self.assertEqual(result["native_cores"], 9)
        validate_inputs(
            self.directory / "render-index.json",
            config,
            self.directory / "evidence.json",
        )
        source["upscaled"] = True
        source_metadata.write_text(json.dumps(source))
        with self.assertRaisesRegex(ValueError, "native source render"):
            export(self.directory, config, source_metadata, review_path, self.prompt)
        source["upscaled"] = False
        source_metadata.write_text(json.dumps(source))
        review["seam_review"]["joins"][0]["passed"] = False
        review_path.write_text(json.dumps(review))
        with self.assertRaisesRegex(ValueError, "explicitly pass"):
            export(self.directory, config, source_metadata, review_path, self.prompt)
        review["seam_review"]["joins"][0]["passed"] = True
        review_path.write_text(json.dumps(review))
        core = self.directory / "textured/x1_y1.png"
        original = core.read_bytes()
        Image.new("RGB", (CORE, CORE), "red").save(core)
        record_path = self.directory / "records/x1_y1.json"
        record = json.loads(record_path.read_text())
        record["source_sha256"] = digest(core)
        record_path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "verified native source crop"):
            export(self.directory, config, source_metadata, review_path, self.prompt)
        core.write_bytes(original)
        record["source_sha256"] = digest(core)
        record_path.write_text(json.dumps(record))
        review["seam_review"]["binding"]["atlas_rgb_sha256"] = "0" * 64
        review_path.write_text(json.dumps(review))
        with self.assertRaisesRegex(ValueError, "different artwork"):
            export(self.directory, config, source_metadata, review_path, self.prompt)


if __name__ == "__main__":
    unittest.main()

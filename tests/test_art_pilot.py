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
    initialize,
    prepare,
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

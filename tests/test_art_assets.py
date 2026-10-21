"""Published preview identity and provenance, not automatic artwork approval."""

import hashlib
import json
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "previews/shinjuku-art"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ArtAssetTests(unittest.TestCase):
    def test_material_assets_match_manifest_and_remain_unapproved(self):
        metadata = json.loads((ART / "style-pilot.json").read_text())
        self.assertEqual(metadata["status"], "complete-unapproved")
        self.assertEqual(metadata["art_approval"], "pending-owner-review")
        self.assertFalse(metadata["ai_styling"])
        for entry in [*metadata["outputs"], metadata["comparison"]]:
            path = ART / entry["path"]
            self.assertEqual(path.resolve().parent, ART.resolve())
            self.assertEqual(digest(path), entry["sha256"])
            with Image.open(path) as image:
                self.assertEqual(image.size, (entry["width"], entry["height"]))
                self.assertEqual(image.info, {})
        self.assertTrue(metadata["provenance"]["vegetation"]["public_data_reference"])

    def test_ai_images_have_distinct_provenance_not_production_approval(self):
        metadata = json.loads((ART / "ai-studies.json").read_text())
        self.assertTrue(metadata["ai_styling"])
        self.assertFalse(metadata["production_release"])
        self.assertEqual(metadata["art_approval"], "pending-owner-review")
        self.assertEqual(len(metadata["candidates"]), 2)
        reference = ART / metadata["reference"]
        self.assertEqual(digest(reference), metadata["reference_public_png_sha256"])
        with Image.open(reference) as image:
            self.assertEqual(
                hashlib.sha256(image.convert("RGB").tobytes()).hexdigest(),
                metadata["reference_rgb_sha256"],
            )
        for candidate in metadata["candidates"]:
            self.assertTrue(candidate["prompt"])
            path = ART / candidate["path"]
            self.assertEqual(path.resolve().parent, ART.resolve())
            self.assertEqual(digest(path), candidate["sha256"])
            with Image.open(path) as image:
                self.assertEqual(list(image.size), candidate["resolution_px"])
                self.assertEqual(image.info, {})

    def test_bounded_coverage_proof_does_not_claim_world_completeness(self):
        audit = json.loads((ART / "source-coverage.json").read_text())
        self.assertTrue(audit["tile_selection_complete"])
        self.assertEqual(audit["required_tiles"], audit["present_tiles"])
        self.assertEqual(audit["missing_tiles"], 0)
        self.assertFalse(audit["source_coverage_verified"])
        self.assertTrue(audit["terrain"]["terrain_surface_support_complete"])
        self.assertTrue(audit["orthophoto"]["orthophoto_support_complete"])

    def test_real_scene_report_preserves_exact_ids_and_unchanged_seam_gate(self):
        report = json.loads((ART / "source-verification.json").read_text())
        config = json.loads((ROOT / "configs/shinjuku-source-check.json").read_text())
        self.assertEqual(report["camera"], config)
        self.assertTrue(report["provenance"]["building_feature_ids_preserved"])
        self.assertTrue(report["shaded_comparison"]["passed"])
        self.assertEqual(
            report["shaded_comparison"]["thresholds_8bit"],
            {"whole_image_mean": 1.0, "boundary_band_mean": 2.0},
        )
        self.assertTrue(
            report["guide_comparison"][
                "categorical_and_numeric_match_reference_exactly"
            ]
        )
        self.assertFalse(report["earlier_256_sample_comparison"]["passed"])
        self.assertFalse(report["source_coverage_verified"])
        self.assertFalse(report["visual_review_approved"])

    def test_published_vegetation_database_is_checksummed_and_not_mit(self):
        root = ROOT / "datasets/shinjuku-vegetation"
        for line in (root / "SHA256SUMS").read_text().splitlines():
            expected, name = line.split(maxsplit=1)
            path = root / name.lstrip("*")
            self.assertEqual(path.resolve().parent, root.resolve())
            self.assertEqual(digest(path), expected)
        self.assertIn("ODbL", (root / "NOTICE.txt").read_text())


if __name__ == "__main__":
    unittest.main()

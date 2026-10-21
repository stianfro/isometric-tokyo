import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, PngImagePlugin

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from render_geometry import (
    assemble_layer,
    check_crop,
    difference_metrics,
    finalize,
    relative_asset,
    validate_partition,
    verify_seams,
)


class GeometryCropTests(unittest.TestCase):
    def test_crop_is_integer_aligned_and_matches_core(self):
        self.assertEqual(
            check_crop([116, 88], [8, 8, 108, 80], [-12, 40, 88, 112]), (8, 8, 108, 80)
        )
        for size, crop, core in [
            ([10, 10], [0, 0, 11, 10], [0, 0, 11, 10]),
            ([10, 10], [0, 0, 10, 10], [0, 0, 9, 10]),
            ([10, 0], [0, 0, 10, 10], [0, 0, 10, 10]),
        ]:
            with self.assertRaises(ValueError):
                check_crop(size, crop, core)

    def test_partition_rejects_holes_overlaps_and_escape(self):
        validate_partition(
            [0, 0, 20, 10],
            [{"bounds_px": [0, 0, 10, 10]}, {"bounds_px": [10, 0, 20, 10]}],
        )
        for chunks in [
            [],
            [{"bounds_px": [0, 0, 10, 10]}],
            [{"bounds_px": [0, 0, 11, 10]}, {"bounds_px": [10, 0, 20, 10]}],
            [{"bounds_px": [-1, 0, 20, 10]}],
        ]:
            with self.assertRaises(ValueError):
                validate_partition([0, 0, 20, 10], chunks)

    def test_paths_cannot_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(
                relative_asset(root, "textured/x0_y0.png"),
                root.resolve() / "textured/x0_y0.png",
            )
            for path in (
                "../private.png",
                "/private.png",
                "textured/../../private.png",
                "a\\b.png",
                "",
            ):
                with self.assertRaises(ValueError):
                    relative_asset(root, path)

    def make_index(self, root):
        chunks = []
        for row in range(3):
            for column in range(3):
                name = f"x{column}_y{row}"
                path = f"overscan/{name}.png"
                (root / "overscan").mkdir(exist_ok=True)
                info = PngImagePlugin.PngInfo()
                info.add_text("File", "private-source-file")
                image = Image.new("RGB", (6, 6))
                image.putdata(
                    [
                        (40 + (column * 4 + x - 1) * 4, 70 + (row * 4 + y - 1) * 4, 110)
                        for y in range(6)
                        for x in range(6)
                    ]
                )
                image.save(root / path, pnginfo=info)
                chunks.append(
                    {
                        "id": name,
                        "bounds_px": [column * 4, row * 4, column * 4 + 4, row * 4 + 4],
                        "crop_box_px": [1, 1, 5, 5],
                        "resolution_px": [6, 6],
                        "frames": {"textured": path},
                    }
                )
        image = Image.new("RGB", (14, 14))
        image.putdata(
            [
                (40 + (x - 1) * 4, 70 + (y - 1) * 4, 110)
                for y in range(14)
                for x in range(14)
            ]
        )
        image.save(root / "overscan/reference.png")
        index = {
            "schema_version": 1,
            "status": "rendered-overscan",
            "layers": ["textured"],
            "fixture": "crossing-towers-shadow-v1",
            "bounds_px": [0, 0, 12, 12],
            "chunks": chunks,
            "reference": {
                "id": "reference",
                "bounds_px": [0, 0, 12, 12],
                "crop_box_px": [1, 1, 13, 13],
                "resolution_px": [14, 14],
                "frames": {"textured": "overscan/reference.png"},
            },
        }
        path = root / "render-index.json"
        path.write_text(json.dumps(index))
        return path

    def test_finalize_strips_metadata_hashes_and_assembles(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.make_index(root)
            index = finalize(path)
            self.assertEqual(index["status"], "complete")
            image_path = root / index["chunks"][0]["outputs"]["textured"]["path"]
            with Image.open(image_path) as image:
                self.assertEqual(image.size, (4, 4))
                self.assertNotIn("File", image.info)
            self.assertEqual(assemble_layer(index, root, "textured").size, (12, 12))
            report = verify_seams(index, root)
            self.assertTrue(report["passed"])
            self.assertFalse(report["source_coverage_verified"])
            self.assertEqual(finalize(path), index)

    def test_finalization_rejects_incomplete_or_wrong_dimensions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.make_index(root)
            Image.new("RGB", (5, 5)).save(root / "overscan/x0_y0.png")
            with self.assertRaisesRegex(ValueError, "dimensions"):
                finalize(path)
            index = json.loads(path.read_text())
            index["status"] = "rendering"
            path.write_text(json.dumps(index))
            with self.assertRaisesRegex(ValueError, "finished"):
                finalize(path)

    def test_modified_pixels_fail_hash_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = finalize(self.make_index(root))
            Image.new("RGB", (4, 4)).save(root / "textured/x0_y0.png")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                assemble_layer(index, root, "textured")

    def test_seam_fixture_blocks_changed_render(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.make_index(root)
            Image.new("RGB", (6, 6), (255, 255, 255)).save(root / "overscan/x1_y1.png")
            index = finalize(path)
            with self.assertRaisesRegex(ValueError, "seam thresholds"):
                verify_seams(index, root)
            self.assertFalse(
                json.loads((root / "seam-verification.json").read_text())["passed"]
            )

    def test_finalization_rejects_duplicate_ids_and_missing_layers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.make_index(root)
            index = json.loads(path.read_text())
            index["chunks"][1]["id"] = index["chunks"][0]["id"]
            path.write_text(json.dumps(index))
            with self.assertRaisesRegex(ValueError, "unique"):
                finalize(path)
            path = self.make_index(root)
            index = json.loads(path.read_text())
            index["layers"].append("geometry")
            path.write_text(json.dumps(index))
            with self.assertRaisesRegex(ValueError, "every declared"):
                finalize(path)

    def test_blank_and_tampered_reference_cannot_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.make_index(root)
            Image.new("RGB", (14, 14), (70, 70, 70)).save(
                root / "overscan/reference.png"
            )
            index = finalize(path)
            with self.assertRaisesRegex(ValueError, "blank"):
                verify_seams(index, root)
            Image.new("RGB", (12, 12), (200, 200, 200)).save(
                root / "textured/reference.png"
            )
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                verify_seams(index, root)

    def test_oversized_diagnostic_assembly_is_rejected(self):
        index = {
            "bounds_px": [0, 0, 5000, 5000],
            "chunks": [{"bounds_px": [0, 0, 5000, 5000]}],
        }
        with self.assertRaisesRegex(ValueError, "budget"):
            assemble_layer(index, Path("unused"), "textured")

    def test_difference_metrics(self):
        black = Image.new("RGB", (5, 5), (0, 0, 0))
        value = Image.new("RGB", (5, 5), (3, 6, 9))
        self.assertEqual(
            difference_metrics(black, value),
            {"mean_absolute_channel_error_8bit": 6.0, "maximum_channel_error_8bit": 9},
        )
        with self.assertRaises(ValueError):
            difference_metrics(black, Image.new("RGB", (2, 2)))


if __name__ == "__main__":
    unittest.main()

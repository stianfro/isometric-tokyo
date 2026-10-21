import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, PngImagePlugin

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from render_style_pilot import finalize, linear_color, output_size, parse_presets


class StylePresetTests(unittest.TestCase):
    def setUp(self):
        self.presets = json.loads(
            (
                Path(__file__).resolve().parents[1] / "configs/style-presets.json"
            ).read_text()
        )

    def test_presets_are_bounded_and_explicitly_unapproved(self):
        result = parse_presets(self.presets)
        self.assertEqual(len(result["presets"]), 3)
        self.assertEqual(result["status"], "unapproved-structural-studies")
        result["presets"][0]["title"] = "Changed"
        self.assertNotEqual(result, self.presets)

    def test_invalid_presets_are_rejected(self):
        mutations = [
            lambda value: value.update(status="approved"),
            lambda value: value["presets"].append(value["presets"][0]),
            lambda value: value["presets"][0].update(id="../escape"),
            lambda value: value["presets"][0].update(facade="#abc"),
            lambda value: value["presets"][0].update(world_strength=float("nan")),
            lambda value: value["presets"][0].update(sun_energy=True),
            lambda value: value["presets"][0].update(roof_normal_z_min=10),
            lambda value: value["presets"][0].update(title="bad\nlabel"),
            lambda value: value["presets"][0].update(unknown=1),
        ]
        for mutate in mutations:
            value = copy.deepcopy(self.presets)
            mutate(value)
            with self.assertRaises(ValueError):
                parse_presets(value)

    def test_palette_conversion_is_linear_srgb(self):
        self.assertEqual(linear_color("#000000"), (0, 0, 0))
        self.assertEqual(linear_color("#ffffff"), (1, 1, 1))
        self.assertAlmostEqual(linear_color("#808080")[0], 0.21586050011389926)
        with self.assertRaises(ValueError):
            linear_color([1, 2, 3])

    def test_preview_dimensions_preserve_the_view(self):
        self.assertEqual(output_size([0, 0, 2400, 2400], 720), (720, 720))
        self.assertEqual(output_size([-100, -200, 2300, 1000], 720), (720, 360))
        for resolution in (0, True, 1025):
            with self.assertRaises(ValueError):
                output_size([0, 0, 2400, 2400], resolution)
        with self.assertRaises(ValueError):
            output_size([0, 0, 100000, 1], 720)

    def test_finalizer_cleans_metadata_without_approving_art(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = Image.new("RGB", (64, 64), "white")
            image.paste((20, 40, 60), (0, 0, 32, 64))
            extra = PngImagePlugin.PngInfo()
            extra.add_text("File", "internal-file-label")
            image.save(root / "paper.png", pnginfo=extra)
            metadata = {
                "schema_version": 1,
                "status": "rendered-unapproved",
                "art_approval": "pending-owner-review",
                "output_resolution_px": [64, 64],
                "outputs": [{"id": "paper", "path": "paper.png", "title": "Paper"}],
            }
            (root / "style-pilot.json").write_text(json.dumps(metadata))
            result = finalize(root)
            self.assertEqual(result["status"], "complete-unapproved")
            self.assertEqual(result["art_approval"], "pending-owner-review")
            self.assertTrue((root / "style-comparison.png").exists())
            with Image.open(root / "paper.png") as clean:
                self.assertEqual(clean.info, {})
                self.assertEqual(clean.tobytes(), image.tobytes())
            self.assertEqual(finalize(root), result)


if __name__ == "__main__":
    unittest.main()

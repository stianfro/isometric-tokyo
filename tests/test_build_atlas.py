"""Pyramid geometry, lossless joins, corruption and release safety."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, PngImagePlugin

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_atlas import (
    build_release,
    level_sizes,
    safe_asset,
    tile_boxes,
    verify_release,
)


class AtlasExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.output = self.root / "atlas"
        self.image = Image.new("RGB", (67, 41))
        self.image.putdata(
            [(x % 256, y % 256, (x + y) % 256) for y in range(41) for x in range(67)]
        )
        info = PngImagePlugin.PngInfo()
        info.add_text("File", "/private/example.blend")
        for name in ("shinjuku-isometric.png", "shinjuku-geometry.png"):
            self.image.save(self.source / name, pnginfo=info)

    def build(self):
        return build_release(self.source, self.output, tile_size=32)

    def test_level_dimensions_and_boxes(self):
        self.assertEqual(level_sizes(5, 3), [(1, 1), (2, 1), (3, 2), (5, 3)])
        self.assertEqual(
            list(tile_boxes(5, 3, 3, 1)), [(0, 0, (0, 0, 4, 3)), (1, 0, (2, 0, 5, 3))]
        )
        for value in (0, -1, 3.4, True):
            with self.assertRaises(ValueError):
                level_sizes(value, 1)
        with self.assertRaises(ValueError):
            list(tile_boxes(2, 2, 1, 1))

    def test_lossless_full_resolution_and_overlap(self):
        self.build()
        assembled = Image.new("RGB", self.image.size)
        for x, y, box in tile_boxes(67, 41, 32):
            with Image.open(self.output / f"textured_files/7/{x}_{y}.webp") as tile:
                self.assertEqual(tile.tobytes(), self.image.crop(box).tobytes())
                assembled.paste(tile, box[:2])
        self.assertEqual(assembled.tobytes(), self.image.tobytes())

    def test_verified_reproducible_release(self):
        first = self.build()
        manifest = (self.output / "manifest.json").read_bytes()
        self.assertEqual(first, verify_release(self.output))
        self.assertEqual(first, self.build())
        self.assertEqual(manifest, (self.output / "manifest.json").read_bytes())
        with Image.open(self.output / "originals/shinjuku-isometric.png") as image:
            image.load()
            self.assertNotIn("File", image.info)
            self.assertIn("Source", image.info)
            self.assertEqual(image.tobytes(), self.image.tobytes())

    def test_checksum_and_missing_asset(self):
        self.build()
        original = self.output / "NOTICE.txt"
        original.write_text("changed")
        with self.assertRaisesRegex(ValueError, "checksum"):
            verify_release(self.output)
        self.build()
        (self.output / "textured_files/0/0_0.webp").unlink()
        with self.assertRaises(FileNotFoundError):
            verify_release(self.output)

    def test_dimension_failure_preserves_previous(self):
        self.build()
        before = (self.output / "manifest.json").read_bytes()
        Image.new("RGB", (3, 3)).save(self.source / "shinjuku-geometry.png")
        with self.assertRaisesRegex(ValueError, "identical"):
            self.build()
        self.assertEqual(before, (self.output / "manifest.json").read_bytes())
        verify_release(self.output)

    def test_config_dimension_mismatch(self):
        config = Path(__file__).resolve().parents[1] / "configs/shinjuku.json"
        with self.assertRaisesRegex(ValueError, "configuration dimensions"):
            build_release(self.source, self.output, config, tile_size=32)
        self.assertFalse(self.output.exists())

    def test_wrong_release_identity(self):
        from build_atlas import digest

        self.build()
        path = self.output / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["id"] = "shinjuku-source-wrong"
        path.write_text(json.dumps(manifest))
        inventory_path = self.output / "assets.json"
        inventory = json.loads(inventory_path.read_text())
        for record in inventory["assets"]:
            if record["path"] == "manifest.json":
                record.update(bytes=path.stat().st_size, sha256=digest(path))
        inventory_path.write_text(json.dumps(inventory))
        with self.assertRaisesRegex(ValueError, "identity"):
            verify_release(self.output)

    def test_unknown_directory_not_replaced(self):
        self.output.mkdir()
        (self.output / "precious.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "Refusing"):
            self.build()
        self.assertEqual((self.output / "precious.txt").read_text(), "keep")

    def test_path_escape_and_wrong_descriptor(self):
        for path in ("../other", "/etc/passwd", "..\\other", ""):
            with self.assertRaises(ValueError):
                safe_asset(self.root, path)
        self.build()
        p = self.output / "manifest.json"
        data = json.loads(p.read_text())
        data["width"] += 1
        p.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "descriptor"):
            verify_release(self.output)


if __name__ == "__main__":
    unittest.main()

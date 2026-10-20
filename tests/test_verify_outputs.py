"""Checks for corrupted inputs and blank or wrongly sized renders."""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_outputs import verify_image, verify_tile_hashes


class VerificationTests(unittest.TestCase):
    def test_hash_integrity(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / "tile").write_bytes(b"test")
            manifest = {
                "tiles": [
                    {
                        "path": "tile",
                        "bytes": 4,
                        "sha256": hashlib.sha256(b"test").hexdigest(),
                    }
                ]
            }
            self.assertEqual(verify_tile_hashes(directory, manifest), 1)
            (directory / "tile").write_bytes(b"fail")
            with self.assertRaises(ValueError):
                verify_tile_hashes(directory, manifest)

    def test_image_checks(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "render.png"
            image = Image.new("RGB", (100, 100), "white")
            image.save(path)
            with self.assertRaises(ValueError):
                verify_image(path, (100, 100))
            ImageDraw.Draw(image).rectangle((10, 10, 50, 50), fill="black")
            image.save(path)
            self.assertEqual(verify_image(path, (100, 100))["size_px"], [100, 100])
            with self.assertRaises(ValueError):
                verify_image(path, (200, 200))


if __name__ == "__main__":
    unittest.main()

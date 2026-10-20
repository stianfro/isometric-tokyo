"""Public preview metadata sanitization must not change image pixels."""

import io
import unittest

from PIL import Image, PngImagePlugin

from scripts.prepare_previews import CREDIT, public_png


class PreviewTests(unittest.TestCase):
    def source(self):
        image = Image.new("RGB", (10, 10), (11, 22, 33))
        info = PngImagePlugin.PngInfo()
        info.add_text("File", "/private/example/source.blend")
        stream = io.BytesIO()
        image.save(stream, format="PNG", pnginfo=info)
        return stream.getvalue()

    def test_pixels_preserved_metadata_replaced(self):
        original = self.source()
        public = public_png(original)
        with Image.open(io.BytesIO(original)) as a, Image.open(io.BytesIO(public)) as b:
            self.assertEqual(a.tobytes(), b.tobytes())
            self.assertEqual(a.size, b.size)
            self.assertNotIn("File", b.info)
            self.assertEqual(b.info["Source"], CREDIT)
        self.assertNotIn(b"/private/example", public)
        self.assertEqual(public_png(public), public)

    def test_invalid_and_truncated(self):
        for content in (b"", b"not png", self.source()[:-3]):
            with self.assertRaises(ValueError):
                public_png(content)

    def test_bad_crc(self):
        source = bytearray(self.source())
        source[20] ^= 1
        with self.assertRaises(ValueError):
            public_png(bytes(source))


if __name__ == "__main__":
    unittest.main()

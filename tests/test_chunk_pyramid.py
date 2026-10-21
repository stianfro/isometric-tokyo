"""Sparse chunk export, exact native pixels and bounded incremental rebuilds."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import chunk_pyramid
from chunk_pyramid import (
    build_pyramid,
    digest,
    dimensions,
    grid_positions,
    tile_box,
    tile_name,
    verify_pyramid,
)
from PIL import Image


class ChunkPyramidTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index_path = self.root / "render-index.json"
        self.output = self.root / "pyramid"
        self.size = 32
        self.index = {
            "schema_version": 1,
            "status": "complete",
            "bounds_px": [-5, -3, 67, 41],
            "chunk_size_px": self.size,
            "layers": ["textured", "geometry"],
            "config_sha256": "a" * 64,
            "chunks": [],
        }
        left, top, right, bottom = self.index["bounds_px"]
        for x, y in grid_positions(self.index["bounds_px"], self.size):
            self.add_chunk(x, y)
        self.save_index()
        self.assertEqual((right - left, bottom - top), (72, 44))

    def add_chunk(self, x, y, color=None):
        extent = self.index["bounds_px"]
        box = [
            max(extent[0], x * self.size),
            max(extent[1], y * self.size),
            min(extent[2], (x + 1) * self.size),
            min(extent[3], (y + 1) * self.size),
        ]
        image = Image.new("RGBA", (box[2] - box[0], box[3] - box[1]))
        if color is None:
            image.putdata(
                [
                    (
                        (xx + box[0]) % 256,
                        (yy + box[1]) % 256,
                        (xx + yy + box[0] + box[1]) % 256,
                        255,
                    )
                    for yy in range(image.height)
                    for xx in range(image.width)
                ]
            )
        else:
            image.paste(color, (0, 0, image.width, image.height))
        chunk = {"id": f"x{x}_y{y}", "bounds_px": box, "outputs": {}}
        for layer in self.index["layers"]:
            path = self.root / f"{layer}-{x}-{y}.png"
            image.save(path)
            chunk["outputs"][layer] = {
                "path": path.name,
                "width": image.width,
                "height": image.height,
                "bytes": path.stat().st_size,
                "sha256": digest(path),
            }
        image.close()
        self.index["chunks"] = [
            old for old in self.index["chunks"] if old["id"] != chunk["id"]
        ]
        self.index["chunks"].append(chunk)
        return chunk

    def save_index(self):
        self.index_path.write_text(json.dumps(self.index))

    def build(self, **kwargs):
        self.save_index()
        return build_pyramid(self.index_path, self.output, tile_size=16, **kwargs)

    def reference(self):
        left, top, right, bottom = self.index["bounds_px"]
        image = Image.new("RGBA", (right - left, bottom - top))
        for chunk in self.index["chunks"]:
            with Image.open(self.root / chunk["outputs"]["textured"]["path"]) as source:
                image.paste(
                    source, (chunk["bounds_px"][0] - left, chunk["bounds_px"][1] - top)
                )
        return image

    def assert_level(self, expected, level, layer="textured"):
        for x, y in grid_positions((0, 0, *expected.size), 16):
            path = self.output / tile_name(layer, level, x, y)
            box = tile_box(expected.size, x, y, 16, 1)
            if path.exists():
                with Image.open(path) as image:
                    self.assertEqual(
                        image.convert("RGBA").tobytes(),
                        expected.crop(box).tobytes(),
                        f"{layer} level {level} tile {x},{y}",
                    )
            else:
                self.assertIsNone(
                    expected.crop(box).getbbox(), "Only uncovered tiles may be absent"
                )

    def test_native_pixels_and_every_reduction_match_reference(self):
        result = self.build()
        self.assertEqual(result["reused_tiles"], 0)
        expected = self.reference()
        sizes = dimensions(*expected.size)
        self.assert_level(expected, len(sizes) - 1)
        for level in range(len(sizes) - 2, -1, -1):
            target = sizes[level]
            padded = Image.new("RGBA", (target[0] * 2, target[1] * 2))
            padded.paste(expected, (0, 0))
            expected.close()
            expected = padded.resize(target, Image.Resampling.BOX)
            padded.close()
            self.assert_level(expected, level)
        expected.close()
        self.assertEqual(
            verify_pyramid(self.output)["tiles"], result["generated_tiles"]
        )

    def test_noop_reuses_all_tiles_and_change_rebuilds_only_affected(self):
        first = self.build()
        before = {
            str(path.relative_to(self.output)): path.read_bytes()
            for path in self.output.rglob("*.webp")
        }
        repeat = self.build()
        self.assertEqual(repeat["id"], first["id"])
        self.assertEqual(repeat["generated_tiles"], 0)
        self.assertEqual(repeat["reused_tiles"], first["tiles"])
        self.add_chunk(0, 0, (200, 20, 90, 255))
        updated = self.build()
        self.assertNotEqual(updated["id"], first["id"])
        self.assertGreater(updated["generated_tiles"], 0)
        self.assertGreater(updated["reused_tiles"], 0)
        # A distant native tile has no source dependency on the changed chunk.
        native_level = len(dimensions(72, 44)) - 1
        remote = tile_name("textured", native_level, 4, 2)
        self.assertEqual((self.output / remote).read_bytes(), before[remote])
        incremental = {
            str(path.relative_to(self.output)): path.read_bytes()
            for path in self.output.rglob("*.webp")
        }
        rebuilt = self.build(incremental=False)
        self.assertEqual(rebuilt["id"], updated["id"])
        self.assertEqual(
            incremental,
            {
                str(path.relative_to(self.output)): path.read_bytes()
                for path in self.output.rglob("*.webp")
            },
        )

    def test_changed_layer_does_not_rebuild_other_layer(self):
        first = self.build()
        chunk = self.index["chunks"][0]
        output = chunk["outputs"]["textured"]
        path = self.root / output["path"]
        Image.new("RGB", (output["width"], output["height"]), "red").save(path)
        output["sha256"], output["bytes"] = digest(path), path.stat().st_size
        updated = self.build()
        self.assertGreaterEqual(updated["reused_tiles"], first["tiles"] // 2)
        self.assertGreater(updated["generated_tiles"], 0)

    def test_sparse_canvas_never_allocates_full_extent(self):
        self.index["bounds_px"] = [0, 0, 1_000_000, 1_000_000]
        self.index["chunks"] = []
        self.add_chunk(0, 0, (100, 150, 200, 255))
        self.add_chunk(31249, 31249, (200, 100, 150, 255))
        allocations = []
        original = Image.new

        def bounded_new(mode, size, *args, **kwargs):
            allocations.append(size[0] * size[1])
            if size[0] * size[1] > 4096:
                raise AssertionError(f"Unbounded allocation: {size}")
            return original(mode, size, *args, **kwargs)

        with patch("chunk_pyramid.Image.new", side_effect=bounded_new):
            result = self.build()
        self.assertLess(result["tiles"], 200)
        self.assertLessEqual(result["largest_window_pixels"], 4 * (16 + 2) ** 2)
        self.assertLessEqual(max(allocations), 4096)
        self.assertEqual(result["largest_source_chunk_pixels"], 32**2)
        self.assertFalse((self.output / "textured_files/20/100_100.webp").exists())
        descriptor = (self.output / "textured.dzi").read_text()
        self.assertEqual(descriptor.count("<DisplayRect"), 2)
        self.assertIn('Width="1000000"', descriptor)

    def test_removal_updates_ancestors_and_sparse_descriptor(self):
        self.build()
        self.index["chunks"] = [
            chunk for chunk in self.index["chunks"] if chunk["id"] != "x1_y0"
        ]
        result = self.build()
        self.assertGreater(result["generated_tiles"], 0)
        native = len(dimensions(72, 44)) - 1
        self.assert_level(self.reference(), native)
        before = {
            str(path.relative_to(self.output)): path.read_bytes()
            for path in self.output.rglob("*.webp")
        }
        self.build(incremental=False)
        self.assertEqual(
            before,
            {
                str(path.relative_to(self.output)): path.read_bytes()
                for path in self.output.rglob("*.webp")
            },
        )

    def test_bad_inputs_do_not_replace_a_valid_pyramid(self):
        self.build()
        before = (self.output / "pyramid-index.json").read_bytes()
        self.index["status"] = "failed"
        with self.assertRaisesRegex(ValueError, "finalized"):
            self.build()
        self.index["status"] = "complete"
        chunk = self.index["chunks"][0]
        self.index["chunks"].append(chunk)
        with self.assertRaisesRegex(ValueError, "unique"):
            self.build()
        self.index["chunks"].pop()
        chunk["outputs"]["textured"]["sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.build()
        self.assertEqual((self.output / "pyramid-index.json").read_bytes(), before)
        verify_pyramid(self.output)

    def test_path_escape_missing_layer_and_alignment_rejected(self):
        output = self.index["chunks"][0]["outputs"]["textured"]
        output["path"] = "../escape.png"
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.build()
        self.index["chunks"][0]["outputs"].pop("textured")
        with self.assertRaisesRegex(ValueError, "every layer"):
            self.build()
        self.index["chunks"][0]["bounds_px"][2] += 1
        with self.assertRaisesRegex(ValueError, "grid"):
            self.build()

    def test_tampered_output_is_not_reused(self):
        self.build()
        path = next(self.output.rglob("*.webp"))
        with Image.open(path) as image:
            size = image.size
        Image.new("RGB", size, "red").save(path, "WEBP", lossless=True)
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.build()
        self.build(incremental=False)
        verify_pyramid(self.output)
        manifest = self.output / "pyramid-index.json"
        data = json.loads(manifest.read_text())
        data["id"] = "wrong"
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "identity"):
            verify_pyramid(self.output)

    def test_configuration_change_changes_identity_and_rebuilds(self):
        first = self.build()
        self.index["config_sha256"] = "c" * 64
        second = self.build()
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(second["reused_tiles"], 0)
        self.assertEqual(second["generated_tiles"], second["tiles"])

    def test_source_change_mid_export_preserves_previous(self):
        self.build()
        before = (self.output / "pyramid-index.json").read_bytes()
        original = chunk_pyramid.native_window
        changed = False

        def changing_window(*args, **kwargs):
            nonlocal changed
            image = original(*args, **kwargs)
            if not changed:
                changed = True
                asset = self.index["chunks"][0]["outputs"]["textured"]
                Image.new("RGB", (asset["width"], asset["height"]), "red").save(
                    self.root / asset["path"]
                )
            return image

        with (
            patch("chunk_pyramid.native_window", side_effect=changing_window),
            self.assertRaisesRegex(ValueError, "changed during"),
        ):
            self.build(incremental=False)
        self.assertEqual((self.output / "pyramid-index.json").read_bytes(), before)
        verify_pyramid(self.output)

    def test_descriptor_sparse_rectangles_are_verified(self):
        self.build()
        path = self.output / "textured.dzi"
        path.write_text(path.read_text().replace('MinLevel="0"', 'MinLevel="1"', 1))
        with self.assertRaisesRegex(ValueError, "sparse coverage"):
            verify_pyramid(self.output)

    def test_unknown_output_and_unsafe_tile_size_rejected(self):
        self.output.mkdir()
        precious = self.output / "keep.txt"
        precious.write_text("keep")
        with self.assertRaisesRegex(ValueError, "Refusing"):
            self.build()
        self.assertEqual(precious.read_text(), "keep")
        for size in (0, 1, 10000, True):
            with self.assertRaises(ValueError):
                build_pyramid(self.index_path, self.output, tile_size=size)


if __name__ == "__main__":
    unittest.main()

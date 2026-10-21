import copy
import json
import struct
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from PIL import Image, PngImagePlugin

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from atlas import load_config, pixel_to_world
from render_pilot_source import (
    INDEX,
    digest,
    finalize,
    grid_plan,
    strip_png_metadata,
    validate_camera,
    write_index,
)

ROOT = Path(__file__).resolve().parents[1]


class PilotSourceTests(unittest.TestCase):
    def test_camera_has_native_resolution_and_original_geographic_frame(self):
        original = load_config(ROOT / "configs/shinjuku.json")
        pilot = load_config(ROOT / "configs/shinjuku-pilot-source.json")
        camera = validate_camera(pilot)
        self.assertEqual(camera["resolution_px"], [3072, 3072])
        self.assertAlmostEqual(camera["ortho_scale_m"], 1260)
        self.assertEqual(pilot.render_samples, 64)
        for x, y in ((0, 0), (0.5, 0.5), (1, 1), (0.25, 0.8)):
            for first, second in zip(
                pixel_to_world(original, (x * 2400, y * 2400), 50),
                pixel_to_world(pilot, (x * 3072, y * 3072), 50),
                strict=True,
            ):
                self.assertAlmostEqual(first, second, places=9)
        with self.assertRaises(ValueError):
            validate_camera(replace(pilot, pixels_per_metre=1))

    def test_exact_nine_cores_and_clipped_context(self):
        chunks = grid_plan(3072, 1024)
        self.assertEqual(len(chunks), 9)
        self.assertEqual(chunks[0]["bounds_px"], [0, 0, 1024, 1024])
        self.assertEqual(chunks[0]["context_bounds_px"], [0, 0, 1280, 1280])
        self.assertEqual(chunks[4]["context_bounds_px"], [768, 768, 2304, 2304])
        self.assertEqual(chunks[4]["core_offset_in_context_px"], [256, 256])
        self.assertEqual(chunks[-1]["context_bounds_px"], [1792, 1792, 3072, 3072])
        for args in ((3073, 1024), (3072, 1024, 1025), (3, True), (6144, 2048)):
            with self.assertRaises(ValueError):
                grid_plan(*args)

    def test_metadata_removal_preserves_pixels_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.png"
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text("filename", "/private/render/source.blend")
            metadata.add_itxt("comment", "not public")
            image = Image.new("RGB", (10, 10), (53, 91, 200))
            image.save(path, pnginfo=metadata)
            strip_png_metadata(path)
            with Image.open(path) as result:
                self.assertEqual(result.tobytes(), image.tobytes())
                self.assertNotIn("filename", result.info)
                self.assertNotIn("comment", result.info)
            clean = path.read_bytes()
            for data in (
                b"bad",
                clean[:-1],
                clean + b"extra",
                clean[:30] + b"x" + clean[31:],
            ):
                path.write_bytes(data)
                with self.assertRaises(ValueError):
                    strip_png_metadata(path)

    def fixture(self, directory):
        size = 12
        image = Image.new("RGB", (size, size))
        for y in range(size):
            for x in range(size):
                image.putpixel((x, y), (x * 13, y * 13, (x + y) * 9))
        outputs = []
        for name in ("source-reference", "atlas-ink"):
            path = directory / (name + ".png")
            image.save(path)
            outputs.append({"id": name, "path": path.name, "sha256": digest(path)})
        guide_path = directory / "guides/depth/reference.f32"
        guide_path.parent.mkdir(parents=True)
        guide_path.write_bytes(struct.pack("<144f", *range(144)))
        semantic = directory / "guides/semantic/reference.png"
        semantic.parent.mkdir(parents=True)
        image.convert("L").save(semantic)
        index = {
            "status": "rendered",
            "upscaled": False,
            "native_resolution_px": [size, size],
            "outputs": outputs,
            "chunks": grid_plan(size, size // 3, size // 3),
            "guide_assets": {
                "depth": {
                    "path": "guides/depth/reference.f32",
                    "sha256": digest(guide_path),
                    "width": size,
                    "height": size,
                    "components": 1,
                },
                "semantic": {
                    "path": "guides/semantic/reference.png",
                    "sha256": digest(semantic),
                    "width": size,
                    "height": size,
                },
            },
        }
        write_index(directory, index)
        return index, image

    def test_finalize_reassembles_pixels_and_preserves_numeric_guides(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, image = self.fixture(directory)
            result = finalize(directory)
            self.assertEqual(result["status"], "complete")
            self.assertTrue(result["core_reassembly_pixel_exact"])
            for chunk in result["chunks"]:
                assets = chunk["assets"]["source-reference"]
                with Image.open(directory / assets["core"]["path"]) as crop:
                    self.assertEqual(
                        crop.tobytes(), image.crop(chunk["bounds_px"]).tobytes()
                    )
                with Image.open(directory / assets["context"]["path"]) as crop:
                    self.assertEqual(
                        crop.tobytes(), image.crop(chunk["context_bounds_px"]).tobytes()
                    )
            data = (
                directory / result["chunks"][4]["guides"]["depth"]["path"]
            ).read_bytes()
            self.assertEqual(
                struct.unpack("<16f", data),
                tuple(float(y * 12 + x) for y in range(4, 8) for x in range(4, 8)),
            )
            self.assertEqual(finalize(directory), result)

    def test_finalizer_rejects_mutated_frames_partition_or_unsafe_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            original, _ = self.fixture(directory)
            mutations = [
                lambda item: item.update(status="rendering"),
                lambda item: item.update(upscaled=True),
                lambda item: item["chunks"][0].update(bounds_px=[0, 0, 3, 4]),
                lambda item: item["outputs"][0].update(sha256="0" * 64),
                lambda item: item["outputs"][0].update(path="../outside.png"),
                lambda item: item["guide_assets"]["depth"].update(width=13),
                lambda item: item["guide_assets"].update(
                    {"../escape": item["guide_assets"]["depth"]}
                ),
            ]
            for mutate in mutations:
                value = copy.deepcopy(original)
                mutate(value)
                (directory / INDEX).write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    finalize(directory)


if __name__ == "__main__":
    unittest.main()

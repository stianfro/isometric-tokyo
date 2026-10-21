import hashlib
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from atlas import load_config, world_to_pixel
from render_guides import (
    canonical_depth_range,
    decode_feature_id,
    encode_feature_id,
    feature_registry,
    pixel_ray,
    source_registry,
    triangle_feature_id,
    write_png,
)


class RenderGuideTests(unittest.TestCase):
    def test_feature_id_encoding_is_exact(self):
        for identifier in (0, 1, 255, 256, 65535, 65536, 2**24 - 1):
            self.assertEqual(
                decode_feature_id(encode_feature_id(identifier)), identifier
            )
        for identifier in (-1, 2**24, 0.5, True):
            with self.assertRaises(ValueError):
                encode_feature_id(identifier)
        with self.assertRaises(ValueError):
            decode_feature_id((0, 256, 1))

    def test_registry_is_independent_of_chunk_order(self):
        first = {
            "b": [{"gml_id": "z", "source_building_id": "official-z"}],
            "a": [{"gml_id": "a", "source_building_id": "official-a"}],
        }
        second = {"a": first["a"], "b": first["b"]}
        self.assertEqual(feature_registry(first), feature_registry(second))
        registry = feature_registry(first)
        self.assertEqual(registry["tile_batch_feature_ids"], {"a": [2], "b": [3]})
        self.assertEqual(registry["records"][2]["gml_id"], "a")
        with self.assertRaises(ValueError):
            feature_registry({"a": [{"gml_id": ""}]})
        with self.assertRaises(ValueError):
            feature_registry(
                {
                    "a": [{"gml_id": "x", "source_building_id": "first"}],
                    "b": [{"gml_id": "x", "source_building_id": "different"}],
                }
            )

    def test_triangle_ids_must_agree_with_source_table(self):
        self.assertEqual(triangle_feature_id([1, 1, 1], [2, 15]), 15)
        for values in ([0, 0, 1], [2, 2, 2], [-1, -1, -1], [1, 1], [True, True, True]):
            with self.assertRaises(ValueError):
                triangle_feature_id(values, [2, 15])

    def test_png_writer_preserves_categorical_and_16bit_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ids = [0, 1, 256, 65536, 2**24 - 1, 1076]
            payload = bytes(
                channel for identity in ids for channel in encode_feature_id(identity)
            )
            write_png(root / "ids.png", (3, 2), payload)
            with Image.open(root / "ids.png") as image:
                self.assertEqual(
                    [decode_feature_id(value) for value in image.getdata()], ids
                )
                self.assertEqual(image.info, {})
            values = [0, 1, 65535, 20000]
            write_png(
                root / "depth.png",
                (2, 2),
                struct.pack(">4H", *values),
                channels=1,
                bits=16,
            )
            with Image.open(root / "depth.png") as image:
                self.assertEqual(list(image.getdata()), values)
            with self.assertRaises(ValueError):
                write_png(root / "bad.png", (1, 1), b"a")

    def test_global_pixel_ray_and_canonical_depth(self):
        config = load_config(
            Path(__file__).resolve().parents[1] / "configs/shinjuku.json"
        )
        near, far = canonical_depth_range(config)
        self.assertLess(near, far)
        from dataclasses import replace

        self.assertEqual(
            canonical_depth_range(replace(config, bounds_px=(800, 800, 1600, 1600))),
            (near, far),
        )
        for pixel in [(0.5, 0.5), (800.5, 1200.5), (2399.5, 2399.5)]:
            origin, direction = pixel_ray(config, pixel, -100)
            self.assertAlmostEqual(sum(value * value for value in direction), 1)
            for distance in (0, 500, 4000):
                point = tuple(origin[i] + distance * direction[i] for i in range(3))
                actual = world_to_pixel(config, point)
                self.assertAlmostEqual(actual[0], pixel[0], places=9)
                self.assertAlmostEqual(actual[1], pixel[1], places=9)

    def test_source_registry_matches_untouched_b3dm_and_glb(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            glb_json = b"{}  "
            glb = struct.pack("<4s4I", b"glTF", 2, 24, 4, 0x4E4F534A) + glb_json
            feature = json.dumps({"BATCH_LENGTH": 2}).encode()
            batch = json.dumps(
                {
                    "gml_id": ["source-b", "source-a"],
                    "uro:BuildingIDAttribute_uro:buildingID": ["b", "a"],
                }
            ).encode()
            payload = (
                struct.pack(
                    "<4s6I",
                    b"b3dm",
                    1,
                    28 + len(feature) + len(batch) + len(glb),
                    len(feature),
                    0,
                    len(batch),
                    0,
                )
                + feature
                + batch
                + glb
            )
            (root / "source.glb").write_bytes(glb)
            (root / "source.b3dm").write_bytes(payload)
            manifest = {
                "tiles": [
                    {
                        "glb_path": "source.glb",
                        "path": "source.b3dm",
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                ]
            }
            (root / "manifest.json").write_text(json.dumps(manifest))
            registry, hashes = source_registry(root / "manifest.json")
            self.assertEqual(
                [
                    value["gml_id"]
                    for value in registry["records"]
                    if value["kind"] == "building"
                ],
                ["source-a", "source-b"],
            )
            self.assertIn("building-b3dm-0", hashes)
            (root / "source.b3dm").write_bytes(payload + b"x")
            with self.assertRaisesRegex(ValueError, "checksum"):
                source_registry(root / "manifest.json")


class GuideArtifactVerificationTests(unittest.TestCase):
    def make_artifacts(self, root):
        import copy

        from render_guides import encode_feature_id, write_png

        records = {
            "records": [
                {"id": 0, "kind": "background"},
                {"id": 1, "kind": "ground"},
                {"id": 2, "kind": "synthetic-object"},
            ]
        }
        (root / "registry.json").write_text(json.dumps(records))
        images = {
            "feature-id": (bytes(encode_feature_id(0) + encode_feature_id(2)), 3, 8),
            "semantic": (bytes([0, 2]), 1, 8),
            "silhouette": (bytes([0, 255]), 1, 8),
            "normal-preview": (bytes([0, 0, 0, 128, 128, 255]), 3, 8),
            "depth-preview": (struct.pack(">2H", 0, 10000), 1, 16),
        }
        assets = {}
        for name, (pixels, channels, bits) in images.items():
            path = root / f"{name}.png"
            write_png(path, (2, 1), pixels, channels=channels, bits=bits)
            assets[name] = {
                "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "width": 2,
                "height": 1,
            }
        for name, values in {"depth": [0, 2500], "normal": [0, 0, 0, 0, 0, 1]}.items():
            path = root / f"{name}.f32"
            payload = struct.pack("<" + "f" * len(values), *values)
            path.write_bytes(payload)
            assets[name] = {
                "path": path.name,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "width": 2,
                "height": 1,
            }
        chunk = {"id": "chunk", "bounds_px": [0, 0, 2, 1], "guides": assets}
        return {
            "bounds_px": [0, 0, 2, 1],
            "guide_metadata": {
                "feature_id": {
                    "registry": "registry.json",
                    "registry_sha256": hashlib.sha256(
                        (root / "registry.json").read_bytes()
                    ).hexdigest(),
                },
                "source_building_registry_count": 0,
            },
            "chunks": [chunk],
            "reference": copy.deepcopy(chunk),
        }

    def test_valid_guides_match_reference_exactly(self):
        from verify_guides import verify_guides

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = verify_guides(
                self.make_artifacts(root), root, compare_reference=True
            )
            self.assertTrue(report["artifacts_valid"])
            self.assertTrue(report["categorical_and_numeric_match_reference_exactly"])

    def test_verifier_rejects_tampering_and_unknown_categories(self):
        from verify_guides import verify_guides

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = self.make_artifacts(root)
            write_png(root / "semantic.png", (2, 1), bytes([0, 6]), channels=1)
            with self.assertRaisesRegex(ValueError, "checksum"):
                verify_guides(index, root)
            index["chunks"][0]["guides"]["semantic"]["sha256"] = hashlib.sha256(
                (root / "semantic.png").read_bytes()
            ).hexdigest()
            with self.assertRaisesRegex(ValueError, "unknown"):
                verify_guides(index, root)

    def test_verifier_rejects_valid_category_for_wrong_source_kind(self):
        from verify_guides import verify_guides

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = self.make_artifacts(root)
            write_png(root / "semantic.png", (2, 1), bytes([0, 3]), channels=1)
            index["chunks"][0]["guides"]["semantic"]["sha256"] = hashlib.sha256(
                (root / "semantic.png").read_bytes()
            ).hexdigest()
            with self.assertRaisesRegex(ValueError, "Source feature kind"):
                verify_guides(index, root)

    def test_verifier_rejects_nonfinite_depth(self):
        from verify_guides import verify_guides

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = self.make_artifacts(root)
            payload = struct.pack("<2f", 0, float("nan"))
            (root / "depth.f32").write_bytes(payload)
            index["chunks"][0]["guides"]["depth"]["sha256"] = hashlib.sha256(
                payload
            ).hexdigest()
            with self.assertRaisesRegex(ValueError, "NaN"):
                verify_guides(index, root)


if __name__ == "__main__":
    unittest.main()

"""Offline tests for bounded tile selection and lossless binary extraction."""

import importlib.util
import json
import math
import struct
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_sample.py"
SPEC = importlib.util.spec_from_file_location("fetch_sample", SCRIPT)
fetch = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fetch
SPEC.loader.exec_module(fetch)


def region(west=139, south=35, east=140, north=36):
    return [math.radians(value) for value in (west, south, east, north)] + [0, 300]


def glb(document):
    encoded = json.dumps(document).encode()
    encoded += b" " * (-len(encoded) % 4)
    return (
        struct.pack("<4s4I", b"glTF", 2, 20 + len(encoded), len(encoded), 0x4E4F534A)
        + encoded
    )


def b3dm(embedded, feature=None, feature_binary=b"", batch=None, batch_binary=b""):
    ft = json.dumps(feature or {"BATCH_LENGTH": 1}).encode()
    bt = json.dumps(batch or {}).encode()
    length = (
        28 + len(ft) + len(feature_binary) + len(bt) + len(batch_binary) + len(embedded)
    )
    return (
        struct.pack(
            "<4s6I",
            b"b3dm",
            1,
            length,
            len(ft),
            len(feature_binary),
            len(bt),
            len(batch_binary),
        )
        + ft
        + feature_binary
        + bt
        + batch_binary
        + embedded
    )


class FetchTests(unittest.TestCase):
    def test_binary_lod_counts_and_bounds(self):
        embedded = glb({"asset": {"version": "2.0"}})
        batch = {"_lod": {"byteOffset": 0, "componentType": "BYTE", "type": "SCALAR"}}
        blob = b3dm(
            embedded, {"BATCH_LENGTH": 3}, batch=batch, batch_binary=bytes([1, 2, 2])
        )
        self.assertEqual(fetch.batch_scalar_values(blob, "_lod"), [1, 2, 2])
        self.assertEqual(fetch.batch_scalar_values(blob, "missing"), [])
        malformed = b3dm(
            embedded, {"BATCH_LENGTH": 4}, batch=batch, batch_binary=bytes([1, 2, 2])
        )
        with self.assertRaises(ValueError):
            fetch.batch_scalar_values(malformed, "_lod")

    def test_intersection_and_degrees(self):
        self.assertTrue(fetch.region_intersects(region(), [139.2, 35.2, 139.4, 35.4]))
        self.assertFalse(fetch.region_intersects(region(), [140.1, 35, 141, 36]))
        with self.assertRaises(ValueError):
            fetch.region_intersects(region(170, -10, -170, 10), [139, 35, 140, 36])

    def test_parent_child_column_major_transform_order(self):
        translation = fetch.IDENTITY.copy()
        translation[12:15] = [10, 20, 30]
        scale = [2, 0, 0, 0, 0, 3, 0, 0, 0, 0, 4, 0, 0, 0, 0, 1]
        product = fetch.matmul(translation, scale)
        self.assertEqual(product[12:15], [10, 20, 30])
        self.assertEqual([product[0], product[5], product[10]], [2, 3, 4])
        self.assertEqual(fetch.matmul(scale, translation)[12:15], [20, 60, 120])

    def test_select_only_intersecting_replacement_leaves(self):
        root = {
            "boundingVolume": {"region": region()},
            "content": {"uri": "coarse.b3dm"},
            "children": [
                {
                    "boundingVolume": {"region": region(139, 35, 139.5, 36)},
                    "content": {"uri": "west.b3dm"},
                },
                {
                    "boundingVolume": {"region": region(139.5, 35, 140, 36)},
                    "content": {"uri": "east.b3dm"},
                },
            ],
        }
        self.assertEqual(
            [
                item["uri"]
                for item in fetch.select_leaf_tiles(
                    {"root": root}, [139.1, 35.1, 139.2, 35.2]
                )
            ],
            ["west.b3dm"],
        )

    def test_content_bounds_can_exclude_intersecting_tile(self):
        root = {
            "boundingVolume": {"region": region()},
            "content": {
                "uri": "data.b3dm",
                "boundingVolume": {"region": region(139.8, 35.8, 139.9, 35.9)},
            },
        }
        self.assertEqual(
            fetch.select_leaf_tiles({"root": root}, [139, 35, 139.2, 35.2]), []
        )

    def test_add_content_and_transform_retained(self):
        transform = fetch.IDENTITY.copy()
        transform[12] = 123
        root = {
            "boundingVolume": {"region": region()},
            "refine": "ADD",
            "transform": transform,
            "content": {"uri": "parent.b3dm"},
            "children": [
                {
                    "boundingVolume": {"region": region()},
                    "content": {"uri": "child.b3dm"},
                }
            ],
        }
        result = fetch.select_leaf_tiles({"root": root}, [139, 35, 140, 36])
        self.assertEqual(len(result), 2)
        self.assertTrue(all(tile["transform"][12] == 123 for tile in result))

    def test_unpack_preserves_glb_and_metadata(self):
        embedded = glb(
            {
                "asset": {"version": "2.0"},
                "extensions": {"CESIUM_RTC": {"center": [1, 2, 3]}},
            }
        )
        actual, feature, batch = fetch.unpack_b3dm(
            b3dm(embedded, batch={"gml_id": ["a"]})
        )
        self.assertEqual(actual, embedded)
        self.assertEqual(feature["BATCH_LENGTH"], 1)
        self.assertEqual(batch["gml_id"], ["a"])
        self.assertEqual(
            fetch.glb_json(actual)["extensions"]["CESIUM_RTC"]["center"], [1, 2, 3]
        )

    def test_binary_feature_rtc(self):
        embedded = glb({"asset": {"version": "2.0"}})
        _, feature, _ = fetch.unpack_b3dm(
            b3dm(
                embedded, {"RTC_CENTER": {"byteOffset": 0}}, struct.pack("<3f", 4, 5, 6)
            )
        )
        self.assertEqual(feature["RTC_CENTER"], [4, 5, 6])

    def test_reject_malformed_binary(self):
        with self.assertRaises(ValueError):
            fetch.unpack_b3dm(b"b3dm")
        with self.assertRaises(ValueError):
            fetch.glb_json(b"glTF")
        blob = bytearray(b3dm(glb({"asset": {"version": "2.0"}})))
        struct.pack_into("<I", blob, 8, 0)
        with self.assertRaises(ValueError):
            fetch.unpack_b3dm(bytes(blob))

    def test_cached_budget_and_https(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing"
            path.write_bytes(b"12345")
            with self.assertRaises(ValueError):
                fetch.Downloader(4).fetch("https://example.org/resource", path)
            with self.assertRaises(ValueError):
                fetch.Downloader(10).fetch("http://example.org/resource", path)


if __name__ == "__main__":
    unittest.main()

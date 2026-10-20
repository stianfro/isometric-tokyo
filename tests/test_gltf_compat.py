import json
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from gltf_compat import remove_external_rtc


class GltfCompatTests(unittest.TestCase):
    def test_only_rtc_metadata_removed(self):
        doc = {
            "asset": {"version": "2.0"},
            "extensionsRequired": ["CESIUM_RTC", "KHR_draco_mesh_compression"],
            "extensionsUsed": ["CESIUM_RTC", "EXT_texture_webp"],
            "extensions": {"CESIUM_RTC": {"center": [1, 2, 3]}},
            "nodes": [{"mesh": 0}],
        }
        raw = json.dumps(doc).encode()
        raw += b" " * (-len(raw) % 4)
        tail = struct.pack("<I4s", 4, b"BIN\x00") + b"test"
        source = (
            struct.pack(
                "<4s4I", b"glTF", 2, 20 + len(raw) + len(tail), len(raw), 0x4E4F534A
            )
            + raw
            + tail
        )
        result = remove_external_rtc(source)
        length = struct.unpack_from("<I", result, 12)[0]
        actual = json.loads(result[20 : 20 + length])
        self.assertEqual(actual["extensionsRequired"], ["KHR_draco_mesh_compression"])
        self.assertEqual(actual["nodes"], doc["nodes"])
        self.assertEqual(result[20 + length :], tail)
        self.assertEqual(struct.unpack_from("<I", result, 8)[0], len(result))

    def test_invalid_header(self):
        with self.assertRaises(ValueError):
            remove_external_rtc(b"broken")

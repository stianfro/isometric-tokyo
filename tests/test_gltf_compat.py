import json
import struct
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from gltf_compat import preserve_unsigned_short_attributes, remove_external_rtc


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


class UnsignedShortAttributeTests(unittest.TestCase):
    def source(self, normalized=False):
        data = {
            "asset": {"version": "2.0"},
            "meshes": [{"primitives": [{"attributes": {"_BATCHID": 0}}]}],
            "accessors": [
                {"componentType": 5123, "type": "SCALAR", "normalized": normalized}
            ],
        }
        raw = json.dumps(data).encode()
        raw += b" " * (-len(raw) % 4)
        return (
            struct.pack("<4s4I", b"glTF", 2, 20 + len(raw), len(raw), 0x4E4F534A) + raw
        )

    def test_normalized_uint16_rejected_without_importer_mutation(self):
        with patch("gltf_compat.import_module") as importer:
            with self.assertRaisesRegex(ValueError, "Normalized"):
                with preserve_unsigned_short_attributes(self.source(normalized=True)):
                    self.fail("normalized attribute was accepted")
            importer.assert_not_called()

    def test_only_scalar_uint16_mapping_changes_and_restores(self):
        def original(component_type, data_type):
            return (component_type, data_type)

        module = SimpleNamespace(get_attribute_type=original)
        with patch("gltf_compat.import_module", return_value=module) as importer:
            with preserve_unsigned_short_attributes(self.source()):
                self.assertEqual(module.get_attribute_type(5123, "SCALAR"), "INT")
                for component_type, data_type in (
                    (5121, "SCALAR"),
                    (5125, "SCALAR"),
                    (5126, "SCALAR"),
                    (5123, "VEC4"),
                ):
                    self.assertEqual(
                        module.get_attribute_type(component_type, data_type),
                        (component_type, data_type),
                    )
            self.assertIs(module.get_attribute_type, original)
            importer.assert_called_once_with("io_scene_gltf2.blender.imp.mesh")

    def test_failure_and_nested_import_scopes_restore_previous_helper(self):
        def original(component_type, data_type):
            return None

        module = SimpleNamespace(get_attribute_type=original)
        with patch("gltf_compat.import_module", return_value=module):
            with self.assertRaisesRegex(RuntimeError, "failed import"):
                with preserve_unsigned_short_attributes(self.source()):
                    outer = module.get_attribute_type
                    with preserve_unsigned_short_attributes(self.source()):
                        self.assertEqual(
                            module.get_attribute_type(5123, "SCALAR"), "INT"
                        )
                    self.assertIs(module.get_attribute_type, outer)
                    raise RuntimeError("failed import")
            self.assertIs(module.get_attribute_type, original)

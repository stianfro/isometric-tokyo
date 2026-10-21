"""Keep an untouched source GLB and prepare a Blender-compatible import copy."""

import json
import struct
from contextlib import contextmanager
from importlib import import_module


def _document(blob):
    if len(blob) < 20:
        raise ValueError("Truncated GLB")
    magic, version, total, json_length, kind = struct.unpack_from("<4s4I", blob)
    if magic != b"glTF" or version != 2 or total != len(blob) or kind != 0x4E4F534A:
        raise ValueError("Invalid GLB header")
    if 20 + json_length > len(blob):
        raise ValueError("Truncated JSON chunk")
    return json.loads(blob[20 : 20 + json_length]), json_length, kind


def remove_external_rtc(blob):
    """Remove CESIUM_RTC only; its ECEF center is applied from the manifest.

    Blender does not support this required glTF extension. Vertex buffers,
    textures and all remaining chunks are retained byte-for-byte.
    """
    data, json_length, kind = _document(blob)
    for key in ("extensionsRequired", "extensionsUsed"):
        if key in data:
            data[key] = [name for name in data[key] if name != "CESIUM_RTC"]
    if "extensions" in data:
        data["extensions"].pop("CESIUM_RTC", None)
    encoded = json.dumps(data, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 4)
    tail = blob[20 + json_length :]
    return (
        struct.pack(
            "<4s4I", b"glTF", 2, 20 + len(encoded) + len(tail), len(encoded), kind
        )
        + encoded
        + tail
    )


@contextmanager
def preserve_unsigned_short_attributes(blob):
    """Keep genuine uint16 custom attributes during a synchronous Blender import.

    Blender 5.2 silently ignores SCALAR UNSIGNED_SHORT custom attributes even
    though its Draco decoder preserves their values. Mapping that exact accessor
    type to Blender INT is lossless: all values are in [0,65535]. This affects the
    importer's attribute allocation only, never source JSON or binary buffers.

    Supply the exact import GLB so normalized values can be rejected before import.
    Scope this around the main-thread bpy glTF import call. Restore the original
    importer helper even on failure. Do not use concurrently with other imports.
    Unknown types, including uint32 values that may overflow INT, stay unchanged.
    Downstream feature-table/range checks remain required before claiming IDs.
    """
    data, _, _ = _document(blob)
    for mesh in data.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            for name, index in primitive.get("attributes", {}).items():
                if not (name.startswith("_") or name.startswith("KHR_")):
                    continue
                accessor = data["accessors"][index]
                if (
                    accessor.get("componentType") == 5123
                    and accessor.get("type") == "SCALAR"
                    and accessor.get("normalized", False)
                ):
                    raise ValueError(
                        "Normalized uint16 custom attributes cannot be mapped losslessly to INT"
                    )
    module = import_module("io_scene_gltf2.blender.imp.mesh")
    original = module.get_attribute_type

    def compatible(component_type, data_type):
        if component_type == 5123 and data_type == "SCALAR":
            return "INT"
        return original(component_type, data_type)

    module.get_attribute_type = compatible
    try:
        yield
    finally:
        module.get_attribute_type = original

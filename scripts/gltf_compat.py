"""Keep an untouched source GLB and prepare a Blender-compatible import copy."""

import json
import struct


def remove_external_rtc(blob):
    """Remove CESIUM_RTC only; its ECEF center is applied from the manifest.

    Blender does not support this required glTF extension. Vertex buffers,
    textures and all remaining chunks are retained byte-for-byte.
    """
    if len(blob) < 20:
        raise ValueError("Truncated GLB")
    magic, version, total, json_length, kind = struct.unpack_from("<4s4I", blob)
    if magic != b"glTF" or version != 2 or total != len(blob) or kind != 0x4E4F534A:
        raise ValueError("Invalid GLB header")
    if 20 + json_length > len(blob):
        raise ValueError("Truncated JSON chunk")
    data = json.loads(blob[20 : 20 + json_length])
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

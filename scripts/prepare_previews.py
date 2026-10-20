"""Export public PNGs with source credits and no local renderer metadata.

This rewrites ancillary metadata only. Encoded image data and colour-management
chunks are retained byte-for-byte, with no image processing or AI treatment.
"""

import argparse
import struct
import zlib
from pathlib import Path

SIGNATURE = b"\x89PNG\r\n\x1a\n"
KEEP = {
    b"IHDR",
    b"PLTE",
    b"IDAT",
    b"IEND",
    b"tRNS",
    b"gAMA",
    b"cHRM",
    b"sRGB",
    b"iCCP",
    b"pHYs",
}
CREDIT = (
    "Tokyo Metropolitan Government / Project PLATEAU, Shinjuku FY2025; "
    "PLATEAU-Ortho 2023. Terrain: PLATEAU | Mapterhorn | 国土地理院. "
    "Selected, transformed, cropped and rendered by isometric.tokyo. "
    "Independent project, no endorsement. No AI styling. "
    "https://www.geospatial.jp/ckan/dataset/plateau-13104-shinjuku-ku-2025 "
    "https://www.mlit.go.jp/plateau/site-policy/ "
    "https://creativecommons.org/licenses/by/4.0/ "
    "See THIRD_PARTY_NOTICES.md for layer-specific terms."
)


def chunk(kind, content):
    return (
        struct.pack(">I", len(content))
        + kind
        + content
        + struct.pack(">I", zlib.crc32(kind + content))
    )


def public_png(blob):
    if not blob.startswith(SIGNATURE):
        raise ValueError("Invalid PNG signature")
    result = bytearray(SIGNATURE)
    offset = len(SIGNATURE)
    while offset < len(blob):
        if offset + 12 > len(blob):
            raise ValueError("Truncated PNG chunk")
        length = struct.unpack_from(">I", blob, offset)[0]
        end = offset + length + 12
        if end > len(blob):
            raise ValueError("Truncated PNG content")
        kind = blob[offset + 4 : offset + 8]
        content = blob[offset + 8 : end - 4]
        crc = struct.unpack_from(">I", blob, end - 4)[0]
        if crc != zlib.crc32(kind + content):
            raise ValueError("Invalid PNG checksum")
        if kind == b"IEND":
            result.extend(
                chunk(b"iTXt", b"Source\x00\x00\x00\x00\x00" + CREDIT.encode("utf-8"))
            )
        if kind in KEEP:
            result.extend(blob[offset:end])
        offset = end
        if kind == b"IEND":
            if offset != len(blob):
                raise ValueError("Unexpected data after IEND")
            return bytes(result)
    raise ValueError("Missing PNG IEND")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("previews"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for name in ("shinjuku-isometric.png", "shinjuku-geometry.png"):
        destination = args.output / name
        destination.write_bytes(public_png((args.source / name).read_bytes()))
        print(f"Exported {destination}, image-data chunks unchanged")


if __name__ == "__main__":
    main()

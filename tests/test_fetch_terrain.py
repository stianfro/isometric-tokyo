"""Offline coverage of TMS geodetic addressing and quantized-mesh decoding."""

import gzip
import struct
import unittest

from scripts.fetch_terrain import (
    DEFAULT_BBOX,
    decode_delta,
    decode_mesh,
    tile_bounds,
    tile_range,
)


def fixture():
    header = bytearray(88)
    struct.pack_into("<ff", header, 24, 50, 80)
    # Triangle vertices: SW at 50 m, SE at 80 m, NW at 50 m.
    channels = (0, 65534, 65533, 0, 0, 65534, 0, 65534, 65533)
    return bytes(header) + struct.pack("<I9HI3H", 3, *channels, 1, 0, 0, 0)


class TerrainTests(unittest.TestCase):
    def test_root_tiles(self):
        self.assertEqual(tile_bounds(0, 0, 0), (-180, -90, 0, 90))
        self.assertEqual(tile_bounds(1, 0, 0), (0, -90, 180, 90))

    def test_sample_fits_four_tiles(self):
        tiles = tile_range(DEFAULT_BBOX, 14)
        self.assertLessEqual(len(tiles), 4)
        bounds = [tile_bounds(x, y, 14) for x, y in tiles]
        self.assertLessEqual(min(b[0] for b in bounds), DEFAULT_BBOX[0])
        self.assertGreaterEqual(max(b[3] for b in bounds), DEFAULT_BBOX[3])

    def test_delta(self):
        self.assertEqual(decode_delta((0, 65534, 65533)), [0, 32767, 0])
        with self.assertRaises(ValueError):
            decode_delta((1,))

    def test_triangle_decode(self):
        vertices, faces = decode_mesh(fixture(), (139, 35, 140, 36))
        self.assertEqual(vertices, [[139, 35, 50], [140, 35, 80], [139, 36, 50]])
        self.assertEqual(faces, [[0, 1, 2]])

    def test_gzip_payload(self):
        self.assertEqual(
            decode_mesh(gzip.compress(fixture()), (0, 0, 1, 1)),
            decode_mesh(fixture(), (0, 0, 1, 1)),
        )

    def test_truncated(self):
        for payload in (b"", fixture()[:-1]):
            with self.assertRaises(ValueError):
                decode_mesh(payload, (0, 0, 1, 1))


if __name__ == "__main__":
    unittest.main()

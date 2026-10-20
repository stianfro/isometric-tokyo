"""Offline tests for the geographic math in the orthophoto downloader."""

import math
import unittest

from scripts.fetch_ground import (
    DEFAULT_BBOX,
    lonlat_to_tile,
    mercator,
    tile_bounds,
    tile_range,
    tile_to_lonlat,
)


class SlippyTileTests(unittest.TestCase):
    def test_world_origin(self):
        self.assertEqual(lonlat_to_tile(0, 0, 1), (1, 1))
        self.assertEqual(tile_to_lonlat(1, 1, 1), (0, 0))

    def test_roundtrip_shinjuku(self):
        x, y = lonlat_to_tile(139.692, 35.69, 18)
        lon, lat = tile_to_lonlat(x, y, 18)
        self.assertAlmostEqual(lon, 139.692, places=10)
        self.assertAlmostEqual(lat, 35.69, places=10)

    def test_bounds_and_adjacent_tiles(self):
        x, y = map(math.floor, lonlat_to_tile(139.692, 35.69, 18))
        west, south, east, north = tile_bounds(x, y, 18)
        self.assertLess(west, 139.692)
        self.assertGreater(east, 139.692)
        self.assertLess(south, 35.69)
        self.assertGreater(north, 35.69)
        self.assertEqual(east, tile_bounds(x + 1, y, 18)[0])
        self.assertEqual(south, tile_bounds(x, y + 1, 18)[3])

    def test_mosaic_covers_requested_box(self):
        xmin, ymin, xmax, ymax = tile_range(DEFAULT_BBOX, 18)
        west, north = tile_to_lonlat(xmin, ymin, 18)
        east, south = tile_to_lonlat(xmax + 1, ymax + 1, 18)
        self.assertLessEqual(west, DEFAULT_BBOX[0])
        self.assertLessEqual(south, DEFAULT_BBOX[1])
        self.assertGreaterEqual(east, DEFAULT_BBOX[2])
        self.assertGreaterEqual(north, DEFAULT_BBOX[3])
        self.assertLessEqual((xmax - xmin + 1) * (ymax - ymin + 1), 100)

    def test_exact_world_tile_range(self):
        self.assertEqual(tile_range((-180, -85, 180, 85), 0), (0, 0, 0, 0))

    def test_invalid_bbox(self):
        with self.assertRaises(ValueError):
            tile_range((140, 35, 139, 36), 18)

    def test_mercator_origin_and_north(self):
        self.assertEqual(mercator(0, 0), (0, 0))
        self.assertGreater(mercator(139.692, 35.69)[1], 0)


if __name__ == "__main__":
    unittest.main()

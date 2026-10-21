"""Offline tests for the geographic math in the orthophoto downloader."""

import io
import math
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import URLError

from scripts.fetch_ground import (
    DEFAULT_BBOX,
    DownloadBudget,
    download_tile,
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


class DownloadBudgetTests(unittest.TestCase):
    class Response(io.BytesIO):
        def __init__(self, payload, known=True):
            super().__init__(payload)
            self.headers = {"Content-Length": str(len(payload))} if known else {}

    def test_known_length_rejected_before_read_and_unknown_cannot_overread(self):
        budget = DownloadBudget(
            4, opener=lambda request, timeout: self.Response(b"12345")
        )
        with self.assertRaisesRegex(ValueError, "budget"):
            budget.fetch("https://example.test/image.png")
        self.assertEqual(budget.network_bytes, 0)
        budget = DownloadBudget(
            4, opener=lambda request, timeout: self.Response(b"12345", False)
        )
        with self.assertRaisesRegex(ValueError, "budget"):
            budget.fetch("https://example.test/image.png")
        self.assertEqual(budget.network_bytes, 4)

    def test_truncated_download_bytes_still_count(self):
        response = self.Response(b"123")
        response.headers["Content-Length"] = "4"
        budget = DownloadBudget(5, opener=lambda request, timeout: response)
        with self.assertRaises(URLError):
            budget.fetch("https://example.test/image.png")
        self.assertEqual(budget.network_bytes, 3)
        budget.opener = lambda request, timeout: self.Response(b"1234")
        with self.assertRaisesRegex(ValueError, "budget"):
            budget.fetch("https://example.test/image.png")
        self.assertEqual(budget.network_bytes, 3)

    def test_concurrent_workers_share_budget(self):
        budget = DownloadBudget(
            8, opener=lambda request, timeout: self.Response(b"12345")
        )
        outcomes = []

        def fetch():
            try:
                outcomes.append(len(budget.fetch("https://example.test/image.png")))
            except ValueError:
                outcomes.append("limited")

        threads = [threading.Thread(target=fetch) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(outcomes.count(5), 1)
        self.assertEqual(budget.network_bytes, 5)
        budget.retain(5)
        with self.assertRaisesRegex(ValueError, "source"):
            budget.retain(4)

    def test_cached_tile_checksums_and_dimensions(self):
        from PIL import Image

        output = io.BytesIO()
        Image.new("RGB", (256, 256), (10, 20, 30)).save(output, format="PNG")
        payload = output.getvalue()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            budget = DownloadBudget(
                opener=lambda request, timeout: self.Response(payload)
            )
            tile = download_tile((0, 0, 0, root, budget))
            self.assertEqual(budget.network_bytes, len(payload))
            cached = DownloadBudget(
                opener=lambda *_a, **_kw: self.fail("unexpected network")
            )
            self.assertEqual(
                download_tile((0, 0, 0, root, cached))["sha256"], tile["sha256"]
            )
            self.assertEqual(cached.source_bytes, len(payload))
            path = root / tile["path"]
            path.write_bytes(payload[:-1] + b"x")
            with self.assertRaisesRegex(ValueError, "checksum"):
                download_tile((0, 0, 0, root, cached))

    def test_nan_and_invalid_byte_limits_rejected(self):
        for value in (0, 100000001, True, float("nan")):
            with self.assertRaises(ValueError):
                DownloadBudget(value)
        for lon, lat in ((float("nan"), 0), (0, float("inf"))):
            with self.assertRaises(ValueError):
                lonlat_to_tile(lon, lat, 17)


if __name__ == "__main__":
    unittest.main()

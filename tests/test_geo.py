import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from geo import ecef, enu_rotation, local_point, tile_matrix


class GeoTests(unittest.TestCase):
    def test_equatorial_ecef(self):
        self.assertAlmostEqual(ecef(0, 0)[0], 6378137)
        self.assertAlmostEqual(ecef(90, 0)[1], 6378137)

    def test_basis_is_orthonormal(self):
        rows = enu_rotation(139.6917, 35.6896)
        for i, a in enumerate(rows):
            for j, b in enumerate(rows):
                self.assertAlmostEqual(
                    sum(x * y for x, y in zip(a, b, strict=True)), int(i == j)
                )

    def test_same_origin_and_height(self):
        p = local_point(139.6917, 35.6896, 100, 139.6917, 35.6896)
        self.assertAlmostEqual(p[0], 0, places=7)
        self.assertAlmostEqual(p[1], 0, places=7)
        self.assertAlmostEqual(p[2], 100, places=7)

    def test_rtc_cancels_without_precision_loss(self):
        identity = [int(r == c) for c in range(4) for r in range(4)]
        m = tile_matrix(identity, ecef(139.6917, 35.6896), 139.6917, 35.6896)
        self.assertLess(math.sqrt(sum(m[r][3] ** 2 for r in range(3))), 1e-8)

    def test_east_and_north_positive(self):
        self.assertGreater(local_point(139.692, 35.6896, 0, 139.6917, 35.6896)[0], 0)
        self.assertGreater(local_point(139.6917, 35.69, 0, 139.6917, 35.6896)[1], 0)


class InverseGeoTests(unittest.TestCase):
    def test_ecef_roundtrip(self):
        from geo import geodetic

        for lon, lat, height in [
            (139.6917, 35.6896, 356),
            (0, 0, -40),
            (-75, -40, 2100),
            (12, 89.999, 5),
        ]:
            actual = geodetic(*ecef(lon, lat, height))
            self.assertAlmostEqual(actual[0], lon, places=9)
            self.assertAlmostEqual(actual[1], lat, places=9)
            self.assertAlmostEqual(actual[2], height, places=6)

    def test_local_roundtrip_across_tokyo(self):
        from geo import local_to_geodetic

        for lon, lat, height in [
            (139.6917, 35.6896, 100),
            (139.82, 35.72, 12),
            (139.52, 35.57, 600),
        ]:
            local = local_point(lon, lat, height, 139.6917, 35.6896)
            actual = local_to_geodetic(local, 139.6917, 35.6896)
            for value, expected in zip(actual, (lon, lat, height), strict=True):
                self.assertAlmostEqual(value, expected, places=6)

    def test_poles_and_invalid_coordinates(self):
        from geo import geodetic

        self.assertAlmostEqual(geodetic(*ecef(0, 90, 125))[2], 125, places=6)
        self.assertAlmostEqual(geodetic(*ecef(0, -90, 125))[1], -90)
        for value in [(0, 0, 0), (float("nan"), 2, 3), (1, 2, float("inf"))]:
            with self.assertRaises(ValueError):
                geodetic(*value)

    def test_web_mercator_uv_uses_nonlinear_latitude(self):
        from geo import mercator_uv

        bounds = (130, 30, 140, 60)
        self.assertEqual(mercator_uv(130, 30, bounds), (0, 0))
        self.assertEqual(mercator_uv(140, 60, bounds), (1, 1))
        u, v = mercator_uv(135, 45, bounds)
        self.assertEqual(u, 0.5)
        self.assertLess(v, 0.45)
        self.assertGreater(mercator_uv(141, 45, bounds)[0], 1)
        with self.assertRaises(ValueError):
            mercator_uv(135, 45, (140, 30, 130, 60))


if __name__ == "__main__":
    unittest.main()

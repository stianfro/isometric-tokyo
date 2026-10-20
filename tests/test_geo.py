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


if __name__ == "__main__":
    unittest.main()

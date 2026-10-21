"""Shared terrain geometry is deterministic and retains the source datum."""

import unittest

from scripts.terrain_patch import (
    TerrainTile,
    boundary_diagnostics,
    grid_bounds,
    interpolation_diagnostics,
    native_patch,
    shared_lattice,
)


def tile(west=0, east=1, offset=0):
    vertices = [
        [west, 0, 50 + west + offset],
        [east, 0, 50 + east + offset],
        [west, 1, 52 + west + offset],
        [east, 1, 52 + east + offset],
    ]
    return TerrainTile([west, 0, east, 1], vertices, [[0, 1, 2], [1, 3, 2]])


class TerrainPatchTests(unittest.TestCase):
    def test_barycentric_heights_retain_original_values(self):
        terrain = tile()
        self.assertAlmostEqual(terrain.sample(0.25, 0.5), 51.25)
        self.assertAlmostEqual(terrain.sample(0, 0), 50)
        self.assertIsNone(terrain.sample(2, 0))
        self.assertEqual(terrain.diagnostics()["reversed_triangles"], 0)

    def test_neighboring_patches_share_identical_global_vertices(self):
        tiles = [tile(), tile(1, 2)]
        left, _, first = shared_lattice(tiles, [0.1, 0.2, 1.1, 0.8], denominator=10)
        right, _, _ = shared_lattice(
            list(reversed(tiles)), [0.9, 0.2, 1.9, 0.8], denominator=10
        )
        a = {(x, y): z for x, y, z in left}
        b = {(x, y): z for x, y, z in right}
        common = a.keys() & b.keys()
        self.assertTrue(common)
        self.assertTrue(all(a[key] == b[key] for key in common))
        self.assertEqual(first["height_adjustment_m"], 0)
        self.assertFalse(first["geometry_height_smoothing_applied"])

    def test_source_boundary_disagreement_is_measured_not_hidden(self):
        tiles = [tile(), tile(1, 2, offset=0.1)]
        report = boundary_diagnostics(tiles)
        self.assertAlmostEqual(report["maximum_height_disagreement_m"], 0.1)
        vertices, _, _ = shared_lattice(tiles, [0, 0, 2, 1], denominator=2)
        boundary = [z for x, y, z in vertices if x == 1 and y == 0]
        self.assertEqual(boundary, [51])

    def test_resampling_error_is_measured_at_native_vertices(self):
        terrain = tile()
        vertices, _, metadata = shared_lattice([terrain], [0, 0, 1, 1], denominator=4)
        error = interpolation_diagnostics([terrain], vertices, metadata)
        self.assertEqual(error["sample_count"], 4)
        self.assertEqual(error["maximum_m"], 0)
        self.assertTrue(error["height_resampling_changes_small_scale_geometry"])

    def test_native_surface_preserves_sharp_interior_vertices(self):
        vertices = [[0, 0, 50], [1, 0, 50], [0, 1, 50], [1, 1, 50], [0.5, 0.5, 75]]
        terrain = TerrainTile(
            [0, 0, 1, 1], vertices, [[0, 1, 4], [1, 3, 4], [3, 2, 4], [2, 0, 4]]
        )
        output, faces, metadata = native_patch([terrain], [0, 0, 1, 1], denominator=10)
        self.assertIn([0.5, 0.5, 75], output)
        self.assertEqual(len(faces), 4)
        self.assertEqual(metadata["open_interior_edges"], 0)
        self.assertFalse(metadata["regular_height_grid_resampling_applied"])

    def test_native_source_edges_weld_only_with_bounded_quantization_error(self):
        vertices, _, metadata = native_patch(
            [tile(), tile(1, 2, offset=0.001)], [0, 0, 2, 1], denominator=10
        )
        self.assertEqual(len(vertices), 6)
        self.assertEqual(metadata["matching_source_edge_pairs"], 1)
        self.assertAlmostEqual(metadata["maximum_measured_join_correction_m"], 0.001)
        with self.assertRaisesRegex(ValueError, "tolerance"):
            native_patch([tile(), tile(1, 2, offset=1)], [0, 0, 2, 1], denominator=10)

    def test_lattice_caps_and_missing_source_fail(self):
        with self.assertRaisesRegex(ValueError, "budget"):
            shared_lattice([tile()], [0, 0, 1, 1], denominator=100, max_vertices=100)
        with self.assertRaisesRegex(ValueError, "cover"):
            shared_lattice([tile()], [0, 0, 2, 1], denominator=2)
        for denominator in (0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                grid_bounds([0, 0, 1, 1], denominator)

    def test_folds_and_holes_are_rejected(self):
        vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]]
        for triangles in ([[0, 2, 1], [1, 3, 2]], [[0, 1, 2]], [[0, 1, 1]]):
            with self.assertRaises(ValueError):
                TerrainTile([0, 0, 1, 1], vertices, triangles)


if __name__ == "__main__":
    unittest.main()

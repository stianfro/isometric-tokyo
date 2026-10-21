"""Bounded native TIN joins and an explicitly experimental lattice sampler."""

from __future__ import annotations

import math


class TerrainTile:
    """Barycentric height lookup over one original, nonoverlapping source TIN."""

    def __init__(self, bounds, vertices, triangles):
        self.bounds = bounds
        self.vertices = vertices
        self.triangles = triangles
        self.bins = {}
        west, south, east, north = bounds
        if not west < east or not south < north:
            raise ValueError("Invalid source terrain bounds")
        self.area = 0.0
        self.max_slope = 0.0
        self.degenerate = 0
        self.reversed = 0
        self.edge_counts = {}
        for number, triangle in enumerate(triangles):
            a, b, c = [vertices[index] for index in triangle]
            area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            if abs(area) < 1e-20:
                self.degenerate += 1
                continue
            self.reversed += area < 0
            self.area += area / 2
            for p, q in ((a, b), (b, c), (c, a)):
                dx = (q[0] - p[0]) * 111320 * math.cos(math.radians((p[1] + q[1]) / 2))
                dy = (q[1] - p[1]) * 111320
                distance = math.hypot(dx, dy)
                if distance:
                    self.max_slope = max(self.max_slope, abs(q[2] - p[2]) / distance)
            for left, right in zip(triangle, triangle[1:] + triangle[:1], strict=True):
                edge = tuple(sorted((left, right)))
                self.edge_counts[edge] = self.edge_counts.get(edge, 0) + 1
            lo = self.bin(min(a[0], b[0], c[0]), min(a[1], b[1], c[1]))
            hi = self.bin(max(a[0], b[0], c[0]), max(a[1], b[1], c[1]))
            for y in range(lo[1], hi[1] + 1):
                for x in range(lo[0], hi[0] + 1):
                    self.bins.setdefault((x, y), []).append(number)
        if (
            self.reversed
            or self.degenerate
            or any(count > 2 for count in self.edge_counts.values())
        ):
            raise ValueError(
                "Source terrain has reversed, degenerate or nonmanifold triangles"
            )
        expected = (east - west) * (north - south)
        if abs(self.area - expected) > expected * 1e-7:
            raise ValueError(
                "Source terrain triangles do not cover their declared tile area"
            )

    def bin(self, lon, lat):
        west, south, east, north = self.bounds
        return (
            min(31, max(0, math.floor((lon - west) / (east - west) * 32))),
            min(31, max(0, math.floor((lat - south) / (north - south) * 32))),
        )

    def sample(self, lon, lat):
        west, south, east, north = self.bounds
        if (
            not west - 1e-12 <= lon <= east + 1e-12
            or not south - 1e-12 <= lat <= north + 1e-12
        ):
            return None
        for number in self.bins.get(self.bin(lon, lat), []):
            a, b, c = [self.vertices[index] for index in self.triangles[number]]
            denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            u = (
                (b[1] - c[1]) * (lon - c[0]) + (c[0] - b[0]) * (lat - c[1])
            ) / denominator
            v = (
                (c[1] - a[1]) * (lon - c[0]) + (a[0] - c[0]) * (lat - c[1])
            ) / denominator
            w = 1 - u - v
            if min(u, v, w) >= -1e-8:
                return u * a[2] + v * b[2] + w * c[2]
        return None

    def diagnostics(self):
        return {
            "reversed_triangles": self.reversed,
            "degenerate_triangles": self.degenerate,
            "nonmanifold_edges": sum(count > 2 for count in self.edge_counts.values()),
            "projected_area_degrees_squared": self.area,
            "maximum_original_edge_rise_run": self.max_slope,
        }


def grid_bounds(bounds, denominator):
    if type(denominator) is not int or not 1 <= denominator <= 360000:
        raise ValueError("Lattice denominator must be an integer in [1,360000]")
    west, south, east, north = bounds
    if (
        not all(math.isfinite(value) for value in bounds)
        or not -180 <= west < east <= 180
        or not -90 <= south < north <= 90
    ):
        raise ValueError("Invalid terrain sampling extent")
    return (
        math.floor(west * denominator),
        math.floor(south * denominator),
        math.ceil(east * denominator),
        math.ceil(north * denominator),
    )


def shared_lattice(tiles, bounds, *, denominator=14400, max_vertices=500000):
    """Sample the same geodetic points regardless of chunk/patch partition.

    Boundary ownership is selected by sorted source bounds, independent of input
    order. Source heights are linearly interpolated, never shifted or smoothed.
    """
    left, bottom, right, top = grid_bounds(bounds, denominator)
    width, height = right - left + 1, top - bottom + 1
    if width * height > max_vertices:
        raise ValueError("Shared terrain lattice exceeds its vertex budget")
    tiles = sorted(tiles, key=lambda tile: tuple(tile.bounds))
    vertices = []
    for y in range(bottom, top + 1):
        for x in range(left, right + 1):
            lon, lat = x / denominator, y / denominator
            value = next(
                (
                    source_height
                    for tile in tiles
                    if (source_height := tile.sample(lon, lat)) is not None
                ),
                None,
            )
            if value is None:
                raise ValueError(
                    "Terrain source does not cover a required lattice vertex"
                )
            if not math.isfinite(value):
                raise ValueError("Nonfinite source terrain height")
            vertices.append([lon, lat, value])
    triangles = []
    for y in range(height - 1):
        for x in range(width - 1):
            start = y * width + x
            triangles.extend(
                [
                    [start, start + 1, start + width],
                    [start + 1, start + width + 1, start + width],
                ]
            )
    return (
        vertices,
        triangles,
        {
            "method": "Shared global geographic lattice, barycentric interpolation of original source TIN",
            "grid_origin_lonlat": [0, 0],
            "grid_step_degrees": 1 / denominator,
            "grid_denominator": denominator,
            "grid_bounds_integer": [left, bottom, right, top],
            "dimensions_vertices": [width, height],
            "height_adjustment_m": 0,
            "geometry_height_smoothing_applied": False,
            "bounds_wsen": [
                left / denominator,
                bottom / denominator,
                right / denominator,
                top / denominator,
            ],
            "boundary_ownership": "First source tile in lexicographic geographic-bounds order",
            "source_tiles": [tile.diagnostics() for tile in tiles],
            "coverage_verified": False,
        },
    )


def boundary_diagnostics(tiles, samples=33):
    """Measure disagreement along adjacent raw tile edges before resampling."""
    pairs = []
    for i, a in enumerate(tiles):
        for b in tiles[i + 1 :]:
            aw, ass, ae, an = a.bounds
            bw, bs, be, bn = b.bounds
            points = []
            if ae == bw or be == aw:
                lo, hi = max(ass, bs), min(an, bn)
                if lo < hi:
                    x = ae if ae == bw else aw
                    points = [
                        (x, lo + (hi - lo) * n / (samples - 1)) for n in range(samples)
                    ]
            if an == bs or bn == ass:
                lo, hi = max(aw, bw), min(ae, be)
                if lo < hi:
                    y = an if an == bs else ass
                    points = [
                        (lo + (hi - lo) * n / (samples - 1), y) for n in range(samples)
                    ]
            deltas = [abs(a.sample(*point) - b.sample(*point)) for point in points]
            if deltas:
                pairs.append(
                    {
                        "bounds_a": list(a.bounds),
                        "bounds_b": list(b.bounds),
                        "samples": len(deltas),
                        "max_height_disagreement_m": max(deltas),
                    }
                )
    return {
        "adjacent_pairs": pairs,
        "maximum_height_disagreement_m": max(
            (p["max_height_disagreement_m"] for p in pairs), default=0
        ),
    }


def interpolation_diagnostics(tiles, vertices, reconciliation):
    """Measure the new lattice surface against native vertices inside its extent."""
    left, bottom, right, top = reconciliation["grid_bounds_integer"]
    width, height = reconciliation["dimensions_vertices"]
    denominator = reconciliation["grid_denominator"]
    errors = []
    for tile in tiles:
        for lon, lat, source_height in tile.vertices:
            gx, gy = lon * denominator, lat * denominator
            if not left <= gx <= right or not bottom <= gy <= top:
                continue
            x, y = (
                min(width - 2, math.floor(gx - left)),
                min(height - 2, math.floor(gy - bottom)),
            )
            u, v = gx - left - x, gy - bottom - y
            start = y * width + x
            z00, z10 = vertices[start][2], vertices[start + 1][2]
            z01, z11 = vertices[start + width][2], vertices[start + width + 1][2]
            if u + v <= 1:
                value = z00 + (z10 - z00) * u + (z01 - z00) * v
            else:
                value = z11 + (z01 - z11) * (1 - u) + (z10 - z11) * (1 - v)
            errors.append(abs(value - source_height))
    errors.sort()
    return {
        "definition": "Absolute lattice surface minus original source height at original vertices inside the bounded lattice",
        "sample_count": len(errors),
        "maximum_m": max(errors, default=0),
        "p50_m": errors[len(errors) // 2] if errors else 0,
        "p95_m": errors[round((len(errors) - 1) * 0.95)] if errors else 0,
        "height_resampling_changes_small_scale_geometry": True,
    }


def native_patch(
    tiles, bounds, *, denominator=14400, max_vertices=500000, max_join_correction_m=0.02
):
    """Retain native TIN detail, weld matching tile edges, then bound the support.

    Same-resolution source edges must have matching horizontal sample positions.
    Only tiny measured quantization differences may be reconciled. Unsupported
    T-junctions fail rather than silently replacing sharp terrain with a grid.
    """
    tiles = sorted(tiles, key=lambda tile: tuple(tile.bounds))
    left, bottom, right, top = grid_bounds(bounds, denominator)
    rectangle = [
        left / denominator,
        bottom / denominator,
        right / denominator,
        top / denominator,
    ]
    quantum = 10**12

    def key(vertex):
        return round(vertex[0] * quantum), round(vertex[1] * quantum)

    checked_pairs = 0
    for i, a in enumerate(tiles):
        for b in tiles[i + 1 :]:
            aw, ass, ae, an = a.bounds
            bw, bs, be, bn = b.bounds
            axis, value = None, None
            if ae == bw and ass == bs and an == bn:
                axis, value = 0, ae
            elif an == bs and aw == bw and ae == be:
                axis, value = 1, an
            if axis is not None:
                aa = {key(point) for point in a.vertices if point[axis] == value}
                bb = {key(point) for point in b.vertices if point[axis] == value}
                if aa != bb:
                    raise ValueError(
                        "Native terrain edges have unmatched horizontal samples; an explicit stitch adapter is required"
                    )
                checked_pairs += 1

    def clip(polygon, axis, value, greater):
        result = []
        for p, q in zip(polygon, polygon[1:] + polygon[:1], strict=True):
            inside_p = p[axis] >= value if greater else p[axis] <= value
            inside_q = q[axis] >= value if greater else q[axis] <= value
            if inside_p:
                result.append(p)
            if inside_p != inside_q:
                fraction = (value - p[axis]) / (q[axis] - p[axis])
                point = [p[j] + fraction * (q[j] - p[j]) for j in range(3)]
                point[axis] = value
                result.append(point)
        return result

    vertices, triangles, lookup = [], [], {}
    corrections = []
    for tile in tiles:
        for face in tile.triangles:
            polygon = [tile.vertices[index] for index in face]
            for axis, value, greater in (
                (0, rectangle[0], True),
                (1, rectangle[1], True),
                (0, rectangle[2], False),
                (1, rectangle[3], False),
            ):
                polygon = clip(polygon, axis, value, greater)
                if not polygon:
                    break
            ids = []
            for point in polygon:
                coordinate = key(point)
                if coordinate not in lookup:
                    lookup[coordinate] = len(vertices)
                    vertices.append(
                        [coordinate[0] / quantum, coordinate[1] / quantum, point[2]]
                    )
                else:
                    correction = abs(vertices[lookup[coordinate]][2] - point[2])
                    if correction > max_join_correction_m:
                        raise ValueError(
                            "Native terrain join correction exceeds the explicit quantization tolerance"
                        )
                    corrections.append(correction)
                identifier = lookup[coordinate]
                if not ids or ids[-1] != identifier:
                    ids.append(identifier)
            if len(ids) > 1 and ids[0] == ids[-1]:
                ids.pop()
            for index in range(1, len(ids) - 1):
                face = [ids[0], ids[index], ids[index + 1]]
                a, b, c = [vertices[item] for item in face]
                area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
                if area > 1e-20:
                    triangles.append(face)
            if len(vertices) > max_vertices:
                raise ValueError("Native terrain support exceeds the vertex budget")
    if not triangles:
        raise ValueError("Native terrain support contains no source surface")
    # The combined mesh has one shared index at every source join. Validate that
    # internal edges have two incident faces and only the outer rectangle is open.
    edges = {}
    for face in triangles:
        for a, b in zip(face, face[1:] + face[:1], strict=True):
            edge = tuple(sorted((a, b)))
            edges[edge] = edges.get(edge, 0) + 1
    internal_open = 0
    for (a, b), count in edges.items():
        if count > 2:
            raise ValueError("Native terrain welding produced a nonmanifold edge")
        if count == 1:
            pa, pb = vertices[a], vertices[b]
            outer = any(
                abs(pa[axis] - value) <= 2e-12 and abs(pb[axis] - value) <= 2e-12
                for axis, value in (
                    (0, rectangle[0]),
                    (1, rectangle[1]),
                    (0, rectangle[2]),
                    (1, rectangle[3]),
                )
            )
            internal_open += not outer
    if internal_open:
        raise ValueError("Native terrain has an open interior edge after welding")
    return (
        vertices,
        triangles,
        {
            "method": "Native source TIN, welded matching tile edges, clipped only at bounded support perimeter",
            "bounds_wsen": rectangle,
            "grid_origin_lonlat": [0, 0],
            "support_grid_step_degrees": 1 / denominator,
            "edge_coordinate_quantization_degrees": 1 / quantum,
            "native_interior_vertices_preserved": True,
            "regular_height_grid_resampling_applied": False,
            "geometry_height_smoothing_applied": False,
            "global_height_adjustment_m": 0,
            "maximum_measured_join_correction_m": max(corrections, default=0),
            "maximum_allowed_join_correction_m": max_join_correction_m,
            "matching_source_edge_pairs": checked_pairs,
            "open_interior_edges": internal_open,
            "source_tiles": [tile.diagnostics() for tile in tiles],
            "coverage_verified": False,
        },
    )

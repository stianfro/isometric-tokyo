"""Small double-precision geographic helpers, independent of Blender."""

import math


def ecef(lon, lat, height=0.0):
    """WGS84 geodetic degrees and ellipsoidal height to ECEF metres."""
    lon, lat = math.radians(lon), math.radians(lat)
    a, e2 = 6378137.0, 6.69437999014e-3
    n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    return (
        (n + height) * math.cos(lat) * math.cos(lon),
        (n + height) * math.cos(lat) * math.sin(lon),
        (n * (1 - e2) + height) * math.sin(lat),
    )


def enu_rotation(lon, lat):
    """ECEF vectors to local east, north, up vectors."""
    lon, lat = math.radians(lon), math.radians(lat)
    return [
        [-math.sin(lon), math.cos(lon), 0.0],
        [-math.sin(lat) * math.cos(lon), -math.sin(lat) * math.sin(lon), math.cos(lat)],
        [math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)],
    ]


def dot(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def local_point(lon, lat, height, origin_lon, origin_lat):
    a, b = ecef(lon, lat, height), ecef(origin_lon, origin_lat)
    delta = [x - y for x, y in zip(a, b, strict=True)]
    return [dot(row, delta) for row in enu_rotation(origin_lon, origin_lat)]


def tile_matrix(transform, rtc, lon, lat):
    """Tile transform + ECEF RTC to ENU, before float32 Blender import.

    glTF Y-up to tile Z-up conversion is performed by Blender's importer.
    The supplied 3D Tiles transform is column-major. All large-coordinate
    cancellation stays in Python float64, not Blender's float32 matrices.
    """
    t = [[transform[c * 4 + r] for c in range(4)] for r in range(4)]
    world_center = [dot(row[:3], rtc) + row[3] for row in t[:3]]
    origin = ecef(lon, lat)
    delta = [a - b for a, b in zip(world_center, origin, strict=True)]
    rotation = enu_rotation(lon, lat)
    out = []
    for row in rotation:
        out.append(
            [sum(row[k] * t[k][c] for k in range(3)) for c in range(3)]
            + [dot(row, delta)]
        )
    return out + [[0.0, 0.0, 0.0, 1.0]]

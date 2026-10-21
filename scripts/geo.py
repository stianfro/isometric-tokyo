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


def geodetic(x, y, z):
    """ECEF metres to WGS84 longitude, latitude and ellipsoidal height.

    Iterating latitude avoids subtracting nearly equal large-coordinate values
    in the local frame. The polar branch avoids dividing by cos(latitude).
    """
    if not all(math.isfinite(value) for value in (x, y, z)):
        raise ValueError("ECEF coordinates must be finite")
    a, e2 = 6378137.0, 6.69437999014e-3
    p = math.hypot(x, y)
    if p < 1e-9:
        if abs(z) < 1e-9:
            raise ValueError("Earth centre has no unique geodetic coordinates")
        return (0.0, math.copysign(90.0, z), abs(z) - a * math.sqrt(1 - e2))
    latitude = math.atan2(z, p * (1 - e2))
    for _ in range(20):
        n = a / math.sqrt(1 - e2 * math.sin(latitude) ** 2)
        updated = math.atan2(z + e2 * n * math.sin(latitude), p)
        if abs(updated - latitude) < 1e-14:
            latitude = updated
            break
        latitude = updated
    n = a / math.sqrt(1 - e2 * math.sin(latitude) ** 2)
    height = (
        p * math.cos(latitude)
        + z * math.sin(latitude)
        - n * (1 - e2 * math.sin(latitude) ** 2)
    )
    return math.degrees(math.atan2(y, x)), math.degrees(latitude), height


def local_to_geodetic(point, origin_lon, origin_lat):
    """Invert local_point using the transpose of the orthonormal ENU basis."""
    rotation = enu_rotation(origin_lon, origin_lat)
    origin = ecef(origin_lon, origin_lat)
    world = [
        origin[axis] + sum(rotation[row][axis] * point[row] for row in range(3))
        for axis in range(3)
    ]
    return geodetic(*world)


def mercator_uv(lon, lat, bounds_wsen):
    """North-up orthophoto UVs, v=0 at south, from geographic coordinates.

    Deliberately do not clamp out-of-bounds vertices: the material must not repeat
    or stretch the last imagery pixel into places without source coverage.
    """
    west, south, east, north = bounds_wsen
    if not all(math.isfinite(value) for value in (lon, lat, west, south, east, north)):
        raise ValueError("Web Mercator coordinates must be finite")
    if not (
        -180 <= west < east <= 180 and -85.05112878 <= south < north <= 85.05112878
    ):
        raise ValueError("Expected nonempty non-wrapping Web Mercator bounds")
    if not -85.05112878 <= lat <= 85.05112878:
        raise ValueError("Latitude outside Web Mercator range")
    y = math.asinh(math.tan(math.radians(lat)))
    ys = math.asinh(math.tan(math.radians(south)))
    yn = math.asinh(math.tan(math.radians(north)))
    return (lon - west) / (east - west), (y - ys) / (yn - ys)

"""Geodesy helpers: distances, bounding boxes and polyline maths.

Everything here works in WGS84 degrees and returns metres.
"""

from __future__ import annotations

import math

EARTH_R = 6371008.8  # mean earth radius, metres
M_PER_DEG_LAT = 111320.0


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in metres."""
    p = math.pi / 180.0
    dlat = (lat2 - lat1) * p
    dlon = (lon2 - lon1) * p
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin(dlon / 2.0) ** 2
    )
    return 2.0 * EARTH_R * math.asin(math.sqrt(min(1.0, a)))


def m_per_deg_lon(lat: float) -> float:
    """Metres per degree of longitude at a given latitude (floored near the poles)."""
    return M_PER_DEG_LAT * max(0.05, math.cos(math.radians(lat)))


def bbox_around(points, margin_m: float):
    """Axis-aligned bbox enclosing `points`, grown by `margin_m`.

    Returns (south, west, north, east) which is Overpass' bbox order.
    """
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    mid_lat = (min(lats) + max(lats)) / 2.0
    dlat = margin_m / M_PER_DEG_LAT
    dlon = margin_m / m_per_deg_lon(mid_lat)
    return (
        max(-90.0, min(lats) - dlat),
        max(-180.0, min(lons) - dlon),
        min(90.0, max(lats) + dlat),
        min(180.0, max(lons) + dlon),
    )


def path_length(coords) -> float:
    """Total length of a [[lat, lon], ...] polyline, in metres."""
    total = 0.0
    for i in range(1, len(coords)):
        total += haversine(coords[i - 1][0], coords[i - 1][1], coords[i][0], coords[i][1])
    return total


def point_to_segment_m(lat, lon, alat, alon, blat, blon):
    """Distance from a point to a segment, plus how far along the segment the
    projection falls (0..1). Uses a local equirectangular projection, which is
    accurate at the scale of a single way segment."""
    mlat = (alat + blat) / 2.0
    kx = m_per_deg_lon(mlat)
    ky = M_PER_DEG_LAT
    px, py = (lon - alon) * kx, (lat - alat) * ky
    bx, by = (blon - alon) * kx, (blat - alat) * ky
    seg_sq = bx * bx + by * by
    t = 0.0 if seg_sq == 0 else max(0.0, min(1.0, (px * bx + py * by) / seg_sq))
    dx, dy = px - bx * t, py - by * t
    return math.hypot(dx, dy), t


def bearing(lat1, lon1, lat2, lon2) -> float:
    """Initial compass bearing from point 1 to point 2, in degrees (0-360)."""
    p = math.pi / 180.0
    dlon = (lon2 - lon1) * p
    y = math.sin(dlon) * math.cos(lat2 * p)
    x = math.cos(lat1 * p) * math.sin(lat2 * p) - math.sin(lat1 * p) * math.cos(
        lat2 * p
    ) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def format_distance(m: float) -> str:
    if m < 1000:
        return f"{int(round(m / 10.0) * 10)} m"
    return f"{m / 1000.0:.1f} km"


def format_duration(s: float) -> str:
    mins = int(round(s / 60.0))
    if mins < 1:
        return "under a minute"
    if mins < 60:
        return f"{mins} min"
    return f"{mins // 60} h {mins % 60:02d} min"


def corridor_polygon(a, b, buffer_m: float):
    """A rectangle hugging the straight line a->b, widened by `buffer_m`.

    For a long trip the axis-aligned bbox of the two endpoints covers a
    ridiculous amount of land (a 150 km diagonal journey spans ~11000 km2),
    which makes Overpass crawl. A corridor around the line asks for a small
    fraction of that while still containing any sanely-routed rail line.

    Returns [(lat, lon), ...] in Overpass `poly:` order.
    """
    mid_lat = (a[0] + b[0]) / 2.0
    kx = m_per_deg_lon(mid_lat)
    ky = M_PER_DEG_LAT

    ax, ay = a[1] * kx, a[0] * ky
    bx, by = b[1] * kx, b[0] * ky
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if length < 1.0:
        dx, dy, length = 1.0, 0.0, 1.0
    ux, uy = dx / length, dy / length       # along the line
    px, py = -uy, ux                        # perpendicular

    corners = []
    for sx, sp in ((-1, +1), (+1, +1), (+1, -1), (-1, -1)):
        x = ax + ux * (sx * buffer_m) + (dx if sx > 0 else 0.0) + px * sp * buffer_m
        y = ay + uy * (sx * buffer_m) + (dy if sx > 0 else 0.0) + py * sp * buffer_m
        corners.append((y / ky, x / kx))
    return corners


def polygon_to_overpass(points) -> str:
    """Format [(lat, lon), ...] for an Overpass `poly:"..."` filter."""
    return " ".join("%.6f %.6f" % (lat, lon) for lat, lon in points)

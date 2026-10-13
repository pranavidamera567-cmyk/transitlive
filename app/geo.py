"""Small geometry helpers for polylines of [lat, lon] points."""
import math

EARTH_M = 6371000.0


def haversine_m(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_M * math.asin(math.sqrt(h))


def bearing(a, b):
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlon = math.radians(b[1] - a[1])
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def slice_points(points, i, j):
    """Points from index i to j inclusive, in travel order (reversed when j < i)."""
    return points[i:j + 1] if i <= j else points[j:i + 1][::-1]


def length_m(points):
    return sum(haversine_m(p, q) for p, q in zip(points, points[1:]))


def point_at(points, fraction):
    """Returns (lat, lon, heading) at `fraction` (0..1) of the polyline's length."""
    if len(points) == 1:
        return points[0][0], points[0][1], 0.0
    fraction = min(max(fraction, 0.0), 1.0)
    target = fraction * length_m(points)
    walked = 0.0
    for p, q in zip(points, points[1:]):
        seg = haversine_m(p, q)
        if seg > 0 and walked + seg >= target:
            t = (target - walked) / seg
            return p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t, bearing(p, q)
        walked += seg
    return points[-1][0], points[-1][1], bearing(points[-2], points[-1])

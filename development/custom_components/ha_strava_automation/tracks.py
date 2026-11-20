"""Bounded, local GPS geometry heuristic; never sends routes to another service."""

from __future__ import annotations

import math
from bisect import bisect_left


def decode_route(encoded: str) -> list[tuple[float, float]]:
    """Decode Strava's precision-5 polyline, rejecting oversized or malformed input."""
    if not isinstance(encoded, str) or len(encoded) > 100000:
        return []
    points, index, latitude, longitude = [], 0, 0, 0
    try:
        while index < len(encoded):
            delta = []
            for _ in range(2):
                value, shift = 0, 0
                while True:
                    digit = ord(encoded[index]) - 63
                    index += 1
                    if not 0 <= digit <= 63 or shift > 30:
                        return []
                    value |= (digit & 31) << shift
                    shift += 5
                    if digit < 32:
                        break
                delta.append(~(value >> 1) if value & 1 else value >> 1)
            latitude += delta[0]
            longitude += delta[1]
            if abs(latitude) > 9000000 or abs(longitude) > 18000000:
                return []
            points.append((latitude / 1e5, longitude / 1e5))
            if len(points) > 10000:
                return []
    except IndexError, TypeError:
        return []
    return points


def gps_track_pattern(activity: dict) -> bool:
    """Compatibility boolean for the independent on-track classification."""
    return track_evidence(activity)["on_track"]


def track_evidence(activity: dict) -> dict:
    """Return independent track evidence; it says nothing about workout intensity."""

    def result(fraction):
        return {"on_track": fraction >= 0.2, "track_fraction": round(fraction, 3)}

    if activity.get("sport_type") != "Run" or activity.get("trainer") or activity.get("manual"):
        return result(0)
    mapping = activity.get("map")
    if not isinstance(mapping, dict):
        return result(0)
    route = decode_route(mapping.get("polyline"))
    if len(route) < 30:
        return result(0)
    if oval_route(route):
        return result(1)
    # Bounded segment scan retains credible track portions of mixed activities.
    lat, lon = route[0]
    xy = [((a - lat) * 111195, (b - lon) * 111195 * math.cos(math.radians(lat))) for a, b in route]
    cumulative = [0.0]
    for a, b in zip(xy, xy[1:]):
        cumulative.append(cumulative[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    if cumulative[-1] <= 0:
        return result(0)
    intervals = []
    for start in range(0, len(route), max(1, len(route) // 40)):
        for size in (600, 900, 1800):
            end = bisect_left(cumulative, cumulative[start] + size)
            if end < len(route) and oval_route(route[start : end + 1]):
                intervals.append((cumulative[start], cumulative[end]))
    covered, previous_end = 0.0, 0.0
    for start, end in sorted(intervals):
        covered += max(0, end - max(start, previous_end))
        previous_end = max(previous_end, end)
    return result(covered / cumulative[-1])


def oval_route(route: list[tuple[float, float]]) -> bool:
    """Check one route segment for repeated 200–400 m stadium-shaped running."""
    if len(route) < 30:
        return False
    lat, lon = route[0]
    points = [
        ((a - lat) * 111195, (b - lon) * 111195 * math.cos(math.radians(lat))) for a, b in route
    ]
    length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))
    # Principal axes allow any orientation. Trim only 2% GPS outliers at each edge.
    cx, cy = (sum(p[i] for p in points) / len(points) for i in range(2))
    xx = sum((x - cx) ** 2 for x, _ in points)
    yy = sum((y - cy) ** 2 for _, y in points)
    xy = sum((x - cx) * (y - cy) for x, y in points)
    angle = 0.5 * math.atan2(2 * xy, xx - yy)
    c, s = math.cos(angle), math.sin(angle)
    rotated = [((x - cx) * c + (y - cy) * s, -(x - cx) * s + (y - cy) * c) for x, y in points]
    xs, ys = (sorted(p[i] for p in rotated) for i in range(2))
    trim = int(len(points) * 0.02)
    left, right, bottom, top = xs[trim], xs[-trim - 1], ys[trim], ys[-trim - 1]
    major, minor = right - left, top - bottom
    perimeter = 2 * (major - minor) + math.pi * minor
    if not (45 <= major <= 230 and 20 <= minor <= 140 and 170 <= perimeter <= 470):
        return False
    if length < 2 * perimeter:
        return False
    # A stadium outline rejects straight shuttles and most street rectangles.
    mx, my = (left + right) / 2, (bottom + top) / 2
    straight, radius = (major - minor) / 2, minor / 2
    on_outline = sum(
        abs(math.hypot(max(abs(x - mx) - straight, 0), y - my) - radius) <= radius * 0.3
        for x, y in rotated
    )
    return on_outline / len(points) >= 0.85

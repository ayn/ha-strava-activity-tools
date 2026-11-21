"""Independent environment enrichment; no Strava API or OAuth dependencies."""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from typing import Any

from .core import nonnegative, parse_time

CREDIT = "Open-Meteo/CAMS · https://open-meteo.com/en/licence"
LEGACY_CREDIT = (
    "Estimates: Open-Meteo / CAMS (https://open-meteo.com/en/docs/air-quality-api; CC BY 4.0)"
)


def aqi_color(value: int) -> str:
    """US EPA AQI bands, not an index conversion."""
    if value <= 50:
        return "💚"
    if value <= 100:
        return "💛"
    if value <= 150:
        return "🧡"
    if value <= 200:
        return "❤️"
    if value <= 300:
        return "💜"
    return "🤎 (Hazardous)"


def uv_color(value: int) -> str:
    """UV risk bands."""
    if value <= 2:
        return "🟢"
    if value <= 5:
        return "🟡"
    if value <= 7:
        return "🟠"
    if value <= 10:
        return "🔴"
    return "🟣"


def missing_environment(description: str | None) -> list[bool]:
    """Which metric lines are missing, independently of where their values come from."""
    return [
        not bool(re.search(rf"(?:^|[\r\n]){label}\s+\d", description or "", re.I))
        for label in ("AQI", "UV Index")
    ]


def normalize_environment_credit(description: str | None) -> str:
    """Replace only exact generated credit lines, preserving unrelated text bytes."""
    output = []
    found = False
    for line in (description or "").splitlines(keepends=True):
        content = line.rstrip("\r\n")
        if content not in (LEGACY_CREDIT, CREDIT):
            output.append(line)
        elif not found:
            output.append(CREDIT + line[len(content) :])
            found = True
    return "".join(output)


def append_environment(
    description: str | None, aqi: Any, uv: Any, source: str | None = None
) -> str:
    """Append missing annotations once and normalize known generated source credits."""
    original = normalize_environment_credit(description)
    lines = []
    for label, raw, color in (("AQI", aqi, aqi_color), ("UV Index", uv, uv_color)):
        if re.search(rf"(?:^|[\r\n]){label}\s+\d", original, re.I):
            continue
        value = nonnegative(raw)
        if value is None:
            raise ValueError("history_unavailable")
        rounded = math.floor(value + 0.5)
        lines.append(f"{label} {rounded} {color(rounded)}")
    if not lines:
        return original
    if source and source not in original.splitlines():
        lines.append(source)
    match = re.search(r"\r\n|\r|\n", original)
    newline = match.group() if match else "\n"
    separator = "" if not original else newline if original.endswith(("\n", "\r")) else newline * 2
    return original + separator + newline.join(lines)


def reading_at(rows: list[dict[str, Any]], start: str) -> float | None:
    """Most recent state at start, including unavailable; never use a future value."""
    when = parse_time(start)
    candidates = []
    for row in rows:
        try:
            timestamp = parse_time(row.get("last_updated") or row["last_changed"])
        except KeyError, ValueError, TypeError:
            continue
        if timestamp <= when:
            candidates.append((timestamp, row.get("state")))
    return nonnegative(max(candidates, key=lambda item: item[0])[1]) if candidates else None


def environment_eligible(
    activity: dict[str, Any],
    now: datetime,
    home: list[float],
    radius_km: float = 15,
) -> bool:
    """Only recent, local, outdoor GPS runs use home weather sensors."""
    if activity.get("sport_type") not in ("Run", "TrailRun"):
        return False
    if activity.get("trainer") or activity.get("manual"):
        return False
    try:
        start = parse_time(activity["start_date"])
        if not now - timedelta(days=7) <= start <= now:
            return False
        gps = activity["start_latlng"]
        for point in (gps, home):
            if (
                len(point) != 2
                or any(isinstance(x, bool) for x in point)
                or not all(math.isfinite(float(x)) for x in point)
            ):
                return False
            if abs(float(point[0])) > 90 or abs(float(point[1])) > 180:
                return False
        a, b = (list(map(math.radians, point)) for point in (gps, home))
        h = (
            math.sin((a[0] - b[0]) / 2) ** 2
            + math.cos(a[0]) * math.cos(b[0]) * math.sin((a[1] - b[1]) / 2) ** 2
        )
        return 12742 * math.asin(min(1, math.sqrt(h))) <= radius_km
    except ValueError, KeyError, TypeError, OverflowError:
        return False

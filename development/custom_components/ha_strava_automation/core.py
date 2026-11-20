"""Portable matching, units, and input validation."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

METERS_PER_MILE = 1609.344


def nonnegative(value: Any) -> float | None:
    """Reject unavailable readings, bools, infinities, and negative distances."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def match_activity(
    activity: dict[str, Any],
    *,
    name_contains: str = "",
    name_exact: str = "",
    device_contains: str = "",
    device_exact: str = "",
    sport_types: list[str] | None = None,
    distance_below_miles: float | None = None,
    exclude_workouts: bool = False,
) -> bool:
    """AND selected filters; title contains is case-insensitive, exact is exact."""
    if exclude_workouts:
        sport = activity.get("sport_type")
        marker = activity.get("workout_type")
        if (sport in {"Run", "TrailRun", "VirtualRun"} and marker == 3) or (
            sport in {"Ride", "MountainBikeRide", "GravelRide", "VirtualRide"} and marker == 12
        ):
            return False
    for key, contains, exact in (
        ("name", name_contains, name_exact),
        ("device_name", device_contains, device_exact),
    ):
        value = activity.get(key) or ""
        if contains and contains.casefold() not in value.casefold():
            return False
        if exact and exact != value:
            return False
    if sport_types and activity.get("sport_type") not in sport_types:
        return False
    if distance_below_miles is not None:
        distance = nonnegative(activity.get("distance"))
        threshold = nonnegative(distance_below_miles)
        if distance is None or threshold is None or distance >= threshold * METERS_PER_MILE:
            return False
    return True


def parse_time(value: str) -> datetime:
    """Require timezone information; local times cannot locate history reliably."""
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone_required")
    return result

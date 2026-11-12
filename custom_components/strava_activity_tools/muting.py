"""Validated mute intents and matching rechecked immediately before a narrow write."""

from typing import Any

from .client import ActivityClient, ActivityError
from .core import match_activity, nonnegative


def mute_parameters(parameters: Any) -> dict:
    if not isinstance(parameters, dict) or set(parameters) - {"filters", "dry_run"}:
        raise ActivityError("invalid_job")
    filters = parameters.get("filters")
    if not isinstance(filters, dict) or len(filters) > 7:
        raise ActivityError("invalid_filters")
    clean = {}
    for key, value in filters.items():
        if key in {"name_contains", "name_exact", "device_contains", "device_exact"}:
            if not isinstance(value, str) or len(value) > 200:
                raise ActivityError("invalid_filters")
            if value.strip():
                clean[key] = value
        elif key == "sport_types":
            if (
                not isinstance(value, list)
                or len(value) > 32
                or any(
                    not isinstance(sport, str) or not sport.strip() or len(sport) > 80
                    for sport in value
                )
            ):
                raise ActivityError("invalid_filters")
            if value:
                clean[key] = value
        elif key == "exclude_workouts":
            if type(value) is not bool:
                raise ActivityError("invalid_filters")
            if value:
                clean[key] = True
        elif key == "distance_below_miles":
            distance = nonnegative(value)
            if distance is None or not 0 < distance <= 200:
                raise ActivityError("invalid_filters")
            clean[key] = distance
        else:
            raise ActivityError("invalid_filters")
    dry_run = parameters.get("dry_run", True)
    if not (clean.keys() - {"exclude_workouts"}) or type(dry_run) is not bool:
        raise ActivityError("invalid_filters")
    return {"filters": clean, "dry_run": dry_run}


async def mute_activity(client: ActivityClient, activity_id: str, parameters: dict) -> dict:
    params = mute_parameters(parameters)
    return await client.transform(
        activity_id,
        lambda _: {"hide_from_home": True},
        params["dry_run"],
        predicate=lambda activity: match_activity(activity, **params["filters"]),
    )

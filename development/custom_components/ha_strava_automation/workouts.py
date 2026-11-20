"""Read-only matching of prescribed quality runs to recorded interval evidence."""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .core import METERS_PER_MILE, nonnegative, parse_time
from .tracks import gps_track_pattern

INTERVAL = re.compile(
    r"(?<![\w.])(\d{1,2})\s*[x×]\s*(\d+(?:\.\d+)?)\s*"
    r"(kilometers?|kilometres?|kms?|k|miles?|mi|meters?|metres?|m|minutes?|mins?|seconds?|secs?|s)\b",
    re.I,
)


def calendar_window(activity: dict[str, Any], fallback_timezone: str) -> tuple[datetime, datetime]:
    """Derive the actual local date from UTC and the activity's IANA timezone."""
    name = activity.get("timezone") or fallback_timezone
    if not isinstance(name, str):
        raise ValueError("invalid_timezone")
    name = name.rsplit(" ", 1)[-1]  # Strava prefixes IANA names with '(GMT+HH:MM)'.
    zone = ZoneInfo(name)
    day = parse_time(activity["start_date"]).astimezone(zone).date()
    return (
        datetime.combine(day, time.min, zone),
        datetime.combine(day + timedelta(days=1), time.min, zone),
    )


def event_on_day(event: dict[str, Any], start: datetime, end: datetime) -> bool:
    """All-day ends are exclusive. Invalid or cancelled entries cannot match."""
    if str(event.get("status", "")).lower() in ("cancelled", "canceled"):
        return False
    try:
        raw_start, raw_end = event["start"], event["end"]
        if len(raw_start) == len(raw_end) == 10:
            return raw_start <= start.date().isoformat() < raw_end
        event_start, event_end = parse_time(raw_start), parse_time(raw_end)
        return event_start < end and event_end > start and event_start < event_end
    except KeyError, TypeError, ValueError:
        return False


def interval_blocks(text: str) -> list[tuple[int, str, float]]:
    """Only explicit non-nested distance/time repeats; no inferred coach units."""
    if re.search(r"\b(?:rest day|off day|cross.train)\b|\d+\s*[x×]\s*\(", text, re.I):
        return []
    blocks = []
    for match in INTERVAL.finditer(text):
        count, value, unit = int(match[1]), float(match[2]), match[3].lower()
        if not 2 <= count <= 50 or value <= 0:
            return []
        if unit.startswith(("min", "sec")) or unit == "s":
            blocks.append((count, "moving_time", value * (60 if unit.startswith("min") else 1)))
        else:
            factor = (
                1000 if unit.startswith("k") else METERS_PER_MILE if unit.startswith("mi") else 1
            )
            blocks.append((count, "distance", value * factor))
    return blocks if len(blocks) <= 10 else []


def lap_evidence(laps: list[dict], blocks: list[tuple[int, str, float]]) -> int:
    """Match ordered work laps separated by observably slower recovery laps."""
    cursor, total = 0, 0
    for count, field, target in blocks:
        matched = 0
        for index in range(cursor, len(laps)):
            lap = laps[index]
            if not isinstance(lap, dict):
                continue
            amount = nonnegative(lap.get(field))
            distance, duration = (
                nonnegative(lap.get("distance")),
                nonnegative(lap.get("moving_time")),
            )
            if amount is None or distance is None or not duration or distance <= 0:
                continue
            if abs(amount - target) > target * 0.08:
                continue
            # Adjacent work/recovery speed contrast rejects uniform GPS auto-laps.
            adjacent = [i for i in (index - 1, index + 1) if 0 <= i < len(laps)]
            recovery = False
            for other in adjacent:
                candidate = laps[other]
                if not isinstance(candidate, dict):
                    continue
                d, t = (
                    nonnegative(candidate.get("distance")),
                    nonnegative(candidate.get("elapsed_time"))
                    or nonnegative(candidate.get("moving_time")),
                )
                if d is not None and t and d / t < distance / duration * 0.8:
                    recovery = True
            if not recovery:
                continue
            matched += 1
            cursor = index + 1
            if matched == count:
                break
        if matched != count:
            return 0
        total += matched
    return total


def suggest_workout(activity: dict[str, Any], events: list[dict], fallback_timezone: str) -> dict:
    """Suggest only. No Strava mutation, calendar updates, or input mutation."""

    def abstain(reason: str) -> dict:
        return {"status": "abstained", "changes": {}, "reason": reason}

    if activity.get("sport_type") not in ("Run", "TrailRun", "VirtualRun"):
        return abstain("unsupported_sport")
    if activity.get("workout_type") not in (None, 0):
        return abstain("existing_label")
    try:
        start, end = calendar_window(activity, fallback_timezone)
    except KeyError, TypeError, ValueError, ZoneInfoNotFoundError:
        return abstain("invalid_activity_time")
    eligible = [e for e in events if isinstance(e, dict) and event_on_day(e, start, end)]
    if not eligible:
        return abstain("no_calendar_match")
    laps = activity.get("laps")
    if not isinstance(laps, list) or not 1 <= len(laps) <= 1000:
        return abstain("insufficient_lap_evidence")
    matches = []
    for event in eligible:
        if re.search(r"(?:^|[:\s-])race\s*:", str(event.get("summary", "")), re.I):
            continue
        text = str(event.get("summary") or "") + "\n" + str(event.get("description") or "")
        blocks = interval_blocks(text)
        if blocks and (count := lap_evidence(laps, blocks)):
            matches.append(count)
    if len(matches) > 1:
        return abstain("ambiguous_calendar")
    if not matches:
        return abstain("insufficient_lap_evidence")
    return {
        "status": "suggested",
        "changes": {"workout_type": 3},
        "reason": "matched_interval_laps",
        "matched_reps": matches[0],
    }


def suggest_track_shoes(activity, events, fallback_timezone, selected, shoes):
    """Suggest the configured pair only; never overwrite a non-default choice."""

    def abstain(reason):
        return {"status": "abstained", "changes": {}, "reason": reason}

    if selected == "disabled":
        return abstain("not_configured")
    if activity.get("sport_type") != "Run" or activity.get("trainer") or activity.get("manual"):
        return abstain("indoor_or_non_running")
    known = {shoe["id"] for shoe in shoes}
    if selected not in known:
        return abstain("shoe_unavailable")
    if activity.get("gear_id") == selected:
        return {"status": "unchanged", "changes": {}, "reason": "already_selected"}
    primary = {shoe["id"] for shoe in shoes if shoe.get("primary")}
    if activity.get("gear_id") is not None and activity["gear_id"] not in primary:
        return abstain("existing_shoe")
    # Existing Workout labels are preserved by the tag matcher but may still use track shoes.
    candidate = {**activity}
    if candidate.get("workout_type") == 3:
        candidate["workout_type"] = None
    evidence = suggest_workout(candidate, events, fallback_timezone)
    if evidence["status"] != "suggested" and activity.get("workout_type") != 3:
        return abstain(evidence["reason"])
    if not gps_track_pattern(activity):
        return abstain("track_not_confirmed")
    return {
        "status": "suggested",
        "changes": {"gear_id": selected},
        "reason": "matched_track_session",
    }

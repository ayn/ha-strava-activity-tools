"""Bounded local display history, separate from durable processing receipts."""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections.abc import Callable
from copy import deepcopy
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .jobs import SAFE_ERRORS

_LOGGER = logging.getLogger(__name__)
LIMIT = 25
RETENTION = 7 * 86400
OPERATIONS = frozenset(
    {
        "update_activity",
        "mute_activity",
        "suggest_workout",
        "match_activity",
        "detect_track",
        "append_environment",
        "fetch",
    }
)
STATUSES = frozenset(
    {
        "updated",
        "unchanged",
        "would_update",
        "suggested",
        "abstained",
        "delivered",
        "skipped_ineligible",
        "queued",
        "failed",
        "matches",
        "no_match",
        "track",
        "not_track",
        "unknown",
    }
)
ERRORS = SAFE_ERRORS | frozenset(
    {
        "invalid_changes",
        "invalid_preconditions",
        "invalid_workout_sport",
        "invalid_weather_config",
        "calendar_unavailable",
        "invalid_activity_time",
        "shoe_catalog_unavailable",
        "invalid_filters",
        "job_storage_unavailable",
        "worker_stopped",
        "queue_full",
    }
)
REASONS = frozenset(
    {
        "unsupported_sport",
        "existing_label",
        "invalid_activity_time",
        "no_calendar_match",
        "insufficient_lap_evidence",
        "ambiguous_calendar",
        "matched_interval_laps",
        "not_configured",
        "indoor_or_non_running",
        "shoe_unavailable",
        "already_selected",
        "existing_shoe",
        "track_not_confirmed",
        "matched_track_session",
        "shoe_catalog_unavailable",
    }
)


def journal_store(hass: HomeAssistant, entry_id: str) -> Store:
    return Store(hass, 1, f"{DOMAIN}.{entry_id}.history", private=True, atomic_writes=True)


def safe_result(result: dict[str, Any], *, include_shoes: bool = True) -> dict[str, Any]:
    """Persist only explicitly selected values, never descriptions, titles or routes."""
    status = result.get("status")
    if status not in STATUSES:
        status = (
            ("matches" if result["matches"] else "no_match")
            if type(result.get("matches")) is bool
            else "unknown"
        )
    output: dict[str, Any] = {"status": status}
    changes = {}
    candidate_changes = result.get("changes")
    for key, value in (candidate_changes if isinstance(candidate_changes, dict) else {}).items():
        if key in ("name", "description", "sport_type"):
            changes[key] = "changed"
        elif key in ("hide_from_home", "trainer", "commute") and type(value) is bool:
            changes[key] = value
        elif key == "workout_type" and type(value) is int and value in (0, 1, 2, 3, 10, 11, 12):
            changes[key] = value
        elif (
            key == "gear_id" and isinstance(value, str) and re.fullmatch(r"[bg][0-9]+|none", value)
        ):
            changes[key] = value
    if changes:
        output["changes"] = changes
    if result.get("reason") in REASONS:
        output["reason"] = result["reason"]
    for key in ("is_workout", "on_track", "matches"):
        if type(result.get(key)) is bool:
            output[key] = result[key]
    for key in ("aqi", "uv", "attempt", "next_retry"):
        value = result.get(key)
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            output[key] = value
    shoes = result.get("track_shoes")
    if include_shoes and isinstance(shoes, dict):
        proposed = safe_result(shoes, include_shoes=False)
        output["track_shoes"] = proposed
    if result.get("error"):
        error = result["error"]
        output["error"] = error if error in ERRORS else "internal_error"
    return output


class RunJournal:
    """Twenty-five entries in each stream, retained for at most seven days."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.store = journal_store(hass, entry.entry_id)
        self.owner = {
            "source_entry_id": entry.data["source_entry_id"],
            "athlete_id": str(entry.data["athlete_id"]),
        }
        self.records: list[dict[str, Any]] = []
        self.listeners: set[Callable[[], None]] = set()
        self._cancel_save: Callable[[], None] | None = None
        self._cancel_expiry: Callable[[], None] | None = None
        self.closed = False
        self._save_lock = asyncio.Lock()

    async def load(self) -> None:
        try:
            data = await self.store.async_load()
            if data is None or data.get("owner") != self.owner:
                return
            records = data["records"]
            if not isinstance(records, list) or len(records) > 2 * LIMIT:
                raise ValueError
            restored = []
            for row in records:
                when = dt_util.parse_datetime(row["timestamp"])
                if when is None or when.tzinfo is None or type(row["preview"]) is not bool:
                    raise ValueError
                if row["operation"] not in OPERATIONS or not re.fullmatch(
                    r"[1-9][0-9]*", row["activity_id"]
                ):
                    raise ValueError
                restored.append(
                    {
                        **safe_result(row),
                        "timestamp": when.isoformat(),
                        "operation": row["operation"],
                        "activity_id": row["activity_id"],
                        "preview": row["preview"],
                    }
                )
            self.records = restored
            self._prune()
            self._arm_expiry()
        except OSError, ValueError, TypeError, KeyError, AttributeError:
            _LOGGER.warning("Display history could not be loaded; job receipts are unchanged")
            self.records = []

    def _prune(self) -> None:
        now = dt_util.utcnow()
        counts = {True: 0, False: 0}
        retained = []
        for row in self.records:
            age = (now - dt_util.parse_datetime(row["timestamp"])).total_seconds()
            preview = row["preview"]
            if 0 <= age < RETENTION and counts[preview] < LIMIT:
                retained.append(row)
                counts[preview] += 1
        self.records = retained

    def recent(self, preview: bool) -> list[dict[str, Any]]:
        self._prune()
        return deepcopy([row for row in self.records if row["preview"] == preview])

    @callback
    def listen(self, listener: Callable[[], None]) -> Callable[[], None]:
        self.listeners.add(listener)
        return lambda: self.listeners.discard(listener)

    @callback
    def record(self, operation: str, activity_id: str, preview: bool, result: dict) -> None:
        if (
            self.closed
            or operation not in OPERATIONS
            or not re.fullmatch(r"[1-9][0-9]*", activity_id)
        ):
            return
        self.records.insert(
            0,
            {
                **safe_result(result),
                "timestamp": dt_util.utcnow().isoformat(),
                "operation": operation,
                "activity_id": activity_id,
                "preview": preview,
            },
        )
        self._prune()
        self._changed()

    @callback
    def _changed(self) -> None:
        for listener in self.listeners.copy():
            try:
                listener()
            except Exception:
                _LOGGER.warning("Display history listener failed; activity processing continues")
        if self._cancel_save is None:
            self._cancel_save = async_call_later(self.hass, 1, self._scheduled_save)
        self._arm_expiry()

    @callback
    def _arm_expiry(self) -> None:
        if self._cancel_expiry:
            self._cancel_expiry()
            self._cancel_expiry = None
        if self.closed or not self.records:
            return
        when = min(dt_util.parse_datetime(row["timestamp"]) for row in self.records)
        self._cancel_expiry = async_track_point_in_utc_time(
            self.hass,
            self._expire,
            when + timedelta(seconds=RETENTION),
        )

    @callback
    def _expire(self, _now: Any) -> None:
        self._cancel_expiry = None
        self._prune()
        self._changed()

    async def _scheduled_save(self, _now: Any) -> None:
        self._cancel_save = None
        await self._save()

    async def _save(self) -> None:
        async with self._save_lock:
            try:
                await self.store.async_save(
                    {"owner": self.owner, "records": deepcopy(self.records)}
                )
            except OSError:
                # Display failures must never cause a successful Strava write to be retried.
                _LOGGER.warning("Display history could not be saved; job receipts are unchanged")

    async def close(self) -> None:
        self.closed = True
        if self._cancel_expiry:
            self._cancel_expiry()
            self._cancel_expiry = None
        if self._cancel_save:
            self._cancel_save()
            self._cancel_save = None
        self._prune()
        await self._save()
        self.listeners.clear()

"""Durable, account-local intents; timers retry known jobs, never discover activities."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any

from .client import ActivityError
from .muting import mute_parameters

TTL = 7 * 86400
CAPACITY = 1024
DELAYS = (60, 300, 1800, 7200)
# A seven-day lifetime bounds repeated failures; allow their counters to reload.
MAX_ATTEMPTS = TTL // min(DELAYS) + 2
RETRYABLE = frozenset(
    {
        "upstream_unavailable",
        "rate_limited",
        "history_unavailable",
        "weather_unavailable",
        "recorder_unavailable",
        "source_not_loaded",
        "activity_changed_retry",
        "verification_failed",
        "activity_not_found",
    }
)
SAFE_ERRORS = RETRYABLE | frozenset(
    {
        "reauth_required",
        "write_scope_or_access_denied",
        "wrong_athlete",
        "invalid_activity_response",
        "invalid_request",
        "home_location_missing",
        "internal_error",
        "writes_disabled",
    }
)


def intent(kind: str, activity_id: str, parameters: dict[str, Any] | None) -> dict[str, Any]:
    """Only persist validated IDs and minimum operation settings."""
    if type(activity_id) is not str or not re.fullmatch(r"[1-9][0-9]*", activity_id):
        raise ActivityError("invalid_activity_id")
    if kind not in ("fetch", "environment", "mute"):
        raise ActivityError("invalid_job")
    params = {} if parameters is None else deepcopy(parameters)
    if not isinstance(params, dict):
        raise ActivityError("invalid_job")
    if kind == "mute":
        params = mute_parameters(params)
    if kind == "fetch" and params:
        raise ActivityError("invalid_job")
    if kind == "environment":
        if params.get("weather_source") == "location_api":
            if set(params) - {"weather_source", "dry_run"}:
                raise ActivityError("invalid_job")
            params.setdefault("dry_run", True)
            if type(params["dry_run"]) is not bool:
                raise ActivityError("invalid_job")
            return {"kind": kind, "activity_id": activity_id, "parameters": params}
        if set(params) - {"aqi_entity", "uv_entity", "radius_km", "dry_run", "external_fallback"}:
            raise ActivityError("invalid_job")
        if "external_fallback" in params and type(params["external_fallback"]) is not bool:
            raise ActivityError("invalid_job")
        for key in ("aqi_entity", "uv_entity"):
            if not isinstance(params.get(key), str) or not re.fullmatch(
                r"sensor\.[a-z0-9_]+", params[key]
            ):
                raise ActivityError("invalid_job")
        params.setdefault("dry_run", True)
        params.setdefault("radius_km", 15)
        if type(params["dry_run"]) is not bool or type(params["radius_km"]) not in (int, float):
            raise ActivityError("invalid_job")
        if not 0 <= params["radius_km"] <= 100:
            raise ActivityError("invalid_job")
    return {"kind": kind, "activity_id": activity_id, "parameters": params}


def identity(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class DurableJobs:
    """Persist intent before side effects and re-read the destination on every attempt."""

    def __init__(
        self,
        store: Any,
        execute: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        notify: Callable[[str, str, str], Awaitable[None]],
        clock: Callable[[], float],
    ) -> None:
        self.store, self.execute, self.notify, self.clock = store, execute, notify, clock
        self.jobs: dict[str, dict[str, Any]] = {}
        self.blocked_until = 0.0
        self._lock = asyncio.Lock()

    async def _save(self, jobs: dict[str, Any], blocked_until: float | None = None) -> None:
        blocked = self.blocked_until if blocked_until is None else blocked_until
        await self.store.async_save(deepcopy({"jobs": jobs, "blocked_until": blocked}))
        self.jobs, self.blocked_until = jobs, blocked

    async def load(self) -> None:
        async with self._lock:
            data = await self.store.async_load()
            if data is None:
                return
            # Corrupt/unknown state fails setup rather than resetting receipts and repeating writes.
            if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
                raise ActivityError("invalid_job_store")
            validated = {}
            expired = []
            expected_keys = {
                "kind",
                "activity_id",
                "parameters",
                "status",
                "created_at",
                "next_retry",
                "attempt",
                "notified",
                "last_error",
            }
            try:
                blocked = data.get("blocked_until", 0.0)
                if type(blocked) not in (int, float) or not math.isfinite(blocked) or blocked < 0:
                    raise ValueError()
                if len(data["jobs"]) > CAPACITY:
                    raise ValueError()
                for key, job in data["jobs"].items():
                    if not isinstance(job, dict) or set(job) != expected_keys:
                        raise ValueError()
                    value = intent(job["kind"], job["activity_id"], job["parameters"])
                    if identity(value) != key or job["status"] not in (
                        "queued",
                        "running",
                        "failed",
                        "done",
                        "skipped",
                    ):
                        raise ValueError()
                    for name in ("created_at", "next_retry"):
                        if (
                            type(job[name]) not in (int, float)
                            or not math.isfinite(job[name])
                            or job[name] < 0
                        ):
                            raise ValueError()
                    if type(job["attempt"]) is not int or not 0 <= job["attempt"] <= MAX_ATTEMPTS:
                        raise ValueError()
                    if type(job["notified"]) is not bool or job["last_error"] not in SAFE_ERRORS | {
                        None
                    }:
                        raise ValueError()
                    if job["created_at"] > self.clock() + 300:
                        raise ValueError()
                    if self.clock() - job["created_at"] >= TTL:
                        expired.append(key)
                        continue
                    validated[key] = deepcopy(job)
                    if job["status"] == "running":
                        validated[key].update(
                            status="queued",
                            next_retry=self.clock(),
                            attempt=max(0, job["attempt"] - 1),
                        )
            except ActivityError, KeyError, TypeError, ValueError:
                raise ActivityError("invalid_job_store") from None
            await self._save(validated, data.get("blocked_until", 0.0))
            for key in expired:
                await self.notify(key, "expired", "")

    async def submit(
        self, kind: str, activity_id: str, parameters: dict[str, Any] | None = None
    ) -> str:
        value = intent(kind, activity_id, parameters)
        key = identity(value)
        async with self._lock:
            jobs = {
                k: deepcopy(v) for k, v in self.jobs.items() if self.clock() - v["created_at"] < TTL
            }
            if key in jobs:
                return key
            if len(jobs) >= CAPACITY:
                raise ActivityError("queue_full")
            jobs[key] = {
                **value,
                "status": "queued",
                "created_at": self.clock(),
                "next_retry": self.clock(),
                "attempt": 0,
                "notified": False,
                "last_error": None,
            }
            await self._save(jobs)
        return key

    async def replay(self, job_id: str) -> None:
        async with self._lock:
            job = self.jobs.get(job_id)
            if job is None or self.clock() - job["created_at"] >= TTL or job["status"] != "failed":
                raise ActivityError("job_not_replayable")
            jobs = deepcopy(self.jobs)
            jobs[job_id].update(status="queued", attempt=0, next_retry=self.clock())
            await self._save(jobs)

    def next_due(self) -> float | None:
        times = [job["created_at"] + TTL for job in self.jobs.values()]
        times.extend(
            max(job["next_retry"], self.blocked_until)
            for job in self.jobs.values()
            if job["status"] == "queued"
        )
        return min(times) if times else None

    async def run_due(self) -> None:
        # One serialized worker per entry. Holding the lock includes side effects:
        # submissions/replays cannot race status changes or duplicate an active job.
        async with self._lock:
            now = self.clock()
            retained = {k: deepcopy(v) for k, v in self.jobs.items() if now - v["created_at"] < TTL}
            if retained != self.jobs:
                expired = set(self.jobs) - set(retained)
                await self._save(retained)
                for key in expired:
                    await self.notify(key, "expired", "")
            if now < self.blocked_until:
                return
            for key in list(self.jobs):
                job = self.jobs[key]
                if job["status"] != "queued" or job["next_retry"] > self.clock():
                    continue
                jobs = deepcopy(self.jobs)
                jobs[key].update(status="running", attempt=job["attempt"] + 1)
                await self._save(jobs)
                try:
                    # The operation re-fetches activity state. Do not store its response.
                    result = await self.execute(deepcopy(self.jobs[key]))
                    if not isinstance(result, dict) or result.get("status") not in (
                        "updated",
                        "unchanged",
                        "delivered",
                        "skipped_ineligible",
                        "would_update",
                    ):
                        raise ActivityError("internal_error")
                except ActivityError as err:
                    category = err.category if err.category in SAFE_ERRORS else "internal_error"
                    retry_after = max(0, err.retry_after)
                except Exception:
                    category, retry_after = "internal_error", 0
                else:
                    jobs = deepcopy(self.jobs)
                    notified = jobs[key]["notified"]
                    skipped = result["status"] in ("skipped_ineligible", "would_update")
                    jobs[key].update(
                        status="skipped" if skipped else "done", last_error=None, notified=False
                    )
                    await self._save(jobs)
                    if notified:
                        await self.notify(key, "expired" if skipped else "recovered", "")
                    continue
                jobs = deepcopy(self.jobs)
                current = jobs[key]
                attempt = current["attempt"]
                retry = category in RETRYABLE and (
                    attempt <= len(DELAYS)
                    or job["kind"] == "mute"
                    or category == "source_not_loaded"
                )
                current.update(
                    status="queued" if retry else "failed",
                    last_error=category,
                    next_retry=self.clock() + max(DELAYS[min(attempt - 1, 3)], retry_after),
                )
                notify = not current["notified"] and (not retry or attempt >= 3)
                current["notified"] = current["notified"] or notify
                blocked = (
                    self.clock() + max(60, retry_after)
                    if category == "rate_limited"
                    else self.blocked_until
                )
                await self._save(jobs, blocked)
                if notify:
                    await self.notify(key, "failed", category)
                if category == "rate_limited":
                    break

"""Home Assistant storage, one-shot retry timers, and local incident notifications."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .client import ActivityError
from .const import DOMAIN, ERROR_EVENT
from .jobs import DurableJobs

RECOVERED_EVENT = "strava_activity_tools_recovered"


class ScopedStore:
    """Changing an entry's source/athlete must never replay another owner's jobs."""

    def __init__(self, store: Store, scope: dict[str, str]) -> None:
        self.store, self.scope = store, scope

    async def async_load(self) -> dict | None:
        data = await self.store.async_load()
        if data is not None and (not isinstance(data, dict) or data.get("owner") != self.scope):
            raise ActivityError("job_owner_mismatch")
        return data

    async def async_save(self, value: dict) -> None:
        await self.store.async_save({**value, "owner": self.scope})


def job_store(hass: HomeAssistant, entry_id: str) -> Store:
    return Store(hass, 1, f"{DOMAIN}.{entry_id}.jobs", private=True, atomic_writes=True)


class JobWorker:
    """Only scheduled pending jobs cause API requests. No activity polling."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, execute: Callable[[dict], Awaitable[dict]]
    ) -> None:
        self.hass, self.entry = hass, entry
        self.queue = DurableJobs(
            ScopedStore(
                job_store(hass, entry.entry_id),
                {
                    "source_entry_id": entry.data["source_entry_id"],
                    "athlete_id": str(entry.data["athlete_id"]),
                },
            ),
            execute,
            self._notify,
            lambda: dt_util.utcnow().timestamp(),
        )
        self._cancel_timer: Callable[[], None] | None = None
        self._task: asyncio.Task | None = None
        self.closed = False
        entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._shutdown))

    def _notification_id(self, key: str) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_{key}"

    @callback
    def _show_failure(self, key: str, category: str) -> None:
        action = (
            "Reconnect the source Strava account, then replay the failed job."
            if category in ("reauth_required", "write_scope_or_access_denied")
            else "Check the source connection and sensor history, then replay the failed job "
            "if needed."
        )
        persistent_notification.async_create(
            self.hass,
            f"Activity processing needs attention ({category}). {action}\n\n"
            f"Use Strava Activity Tools: List jobs / Replay job in Developer Tools → Actions.\n"
            f"Account entry: `{self.entry.entry_id}`\nJob: `{key}`",
            title="Strava Activity Tools: processing delayed",
            notification_id=self._notification_id(key),
        )

    async def _notify(self, key: str, state: str, category: str) -> None:
        if state == "expired":
            persistent_notification.async_dismiss(self.hass, self._notification_id(key))
            return
        if state == "recovered":
            persistent_notification.async_dismiss(self.hass, self._notification_id(key))
        else:
            self._show_failure(key, category)
        self.hass.bus.async_fire(
            RECOVERED_EVENT if state == "recovered" else ERROR_EVENT,
            {
                "config_entry_id": self.entry.entry_id,
                "job_id": key,
                "activity_id": self.queue.jobs[key]["activity_id"],
                "category": category,
            },
        )

    async def load(self) -> None:
        await self.queue.load()
        for key, job in self.queue.jobs.items():
            if job["notified"]:
                self._show_failure(key, job["last_error"])
            else:
                persistent_notification.async_dismiss(self.hass, self._notification_id(key))
        self.arm()

    @callback
    def arm(self) -> None:
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None
        if self.closed:
            return
        when = self.queue.next_due()
        if when is None:
            return
        # A single bounded timer wakes only for an existing job or retention cleanup.
        self._cancel_timer = async_track_point_in_utc_time(
            self.hass,
            self._wake,
            datetime.fromtimestamp(max(when, dt_util.utcnow().timestamp() + 0.05), UTC),
        )

    @callback
    def _wake(self, _now: datetime) -> None:
        self._cancel_timer = None
        if not self.closed:
            self.entry.async_create_background_task(self.hass, self.process(), f"{DOMAIN}_retry")

    async def _drain(self) -> None:
        try:
            await self.queue.run_due()
        except OSError, ActivityError:
            self._storage_error()
        finally:
            self.arm()

    @callback
    def _storage_error(self) -> None:
        self.closed = True
        self.arm()
        persistent_notification.async_create(
            self.hass,
            "The job store could not be saved safely. Processing is stopped. Check HA storage, "
            "then reload Strava Activity Tools. Saved jobs will be rechecked before writing.",
            title="Strava Activity Tools: storage needs attention",
            notification_id=f"{DOMAIN}_{self.entry.entry_id}_storage",
        )

    async def process(self) -> None:
        if self.closed:
            raise ActivityError("worker_stopped")
        if self._task is None or self._task.done():
            self._task = self.entry.async_create_background_task(
                self.hass, self._drain(), f"{DOMAIN}_worker"
            )
        # Caller cancellation/disconnection must not cancel a running write.
        await asyncio.shield(self._task)

    async def submit(self, kind: str, activity_id: str, parameters: dict | None = None) -> dict:
        if self.closed:
            raise ActivityError("worker_stopped")
        try:
            key = await self.queue.submit(kind, activity_id, parameters)
        except OSError:
            self._storage_error()
            raise ActivityError("job_storage_unavailable") from None
        await self.process()
        if self.closed:
            raise ActivityError("job_storage_unavailable")
        return {"job_id": key, "activity_id": activity_id, "status": self.queue.jobs[key]["status"]}

    async def replay(self, key: str) -> dict:
        if self.closed:
            raise ActivityError("worker_stopped")
        await self.queue.replay(key)
        await self.process()
        return {"job_id": key, "status": self.queue.jobs[key]["status"]}

    def status(self) -> dict[str, Any]:
        return {
            "jobs": [
                {
                    "job_id": key,
                    **{
                        name: job[name]
                        for name in (
                            "activity_id",
                            "kind",
                            "status",
                            "created_at",
                            "next_retry",
                            "attempt",
                            "last_error",
                        )
                    },
                }
                for key, job in self.queue.jobs.items()
            ],
            "blocked_until": self.queue.blocked_until,
            "stopped": self.closed,
        }

    async def _shutdown(self, _event: Any) -> None:
        await self.close()

    async def close(self) -> None:
        self.closed = True
        self.arm()
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

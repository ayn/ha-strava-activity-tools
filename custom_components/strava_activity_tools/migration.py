"""Copy validated completed receipts using native HA stores."""

from copy import deepcopy

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .client import ActivityError
from .jobs import CAPACITY, DurableJobs
from .worker import ScopedStore, job_store

LEGACY_DOMAIN = "activity_tools"


def legacy_entry(hass: HomeAssistant, legacy_id: str, scope: dict) -> ConfigEntry:
    """Only accept a disabled legacy entry belonging to the same source and athlete."""
    old = hass.config_entries.async_get_entry(legacy_id)
    if (
        old is None
        or old.domain != LEGACY_DOMAIN
        or old.disabled_by is None
        or any(old.data.get(key) != value for key, value in scope.items())
    ):
        raise ActivityError("legacy_unavailable")
    return old


class _MemoryStore:
    """Let the queue validate a snapshot without changing the source store."""

    def __init__(self, value: dict | None) -> None:
        self.value = deepcopy(value)

    async def async_load(self):
        return deepcopy(self.value)

    async def async_save(self, value):
        self.value = deepcopy(value)


async def _unused(*args):
    """Validation must never execute a job or publish a notification."""
    return None


async def _validate(data: dict | None) -> DurableJobs:
    queue = DurableJobs(_MemoryStore(data), _unused, _unused, lambda: dt_util.utcnow().timestamp())
    await queue.load()
    return queue


async def migrate_legacy_receipts(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Refuse unfinished work; retain the old store as an unchanged rollback point."""
    scope = {key: entry.data[key] for key in ("source_entry_id", "athlete_id")}
    old = legacy_entry(hass, entry.data["legacy_entry_id"], scope)
    source = ScopedStore(
        Store(hass, 1, f"{LEGACY_DOMAIN}.{old.entry_id}.jobs", private=True, atomic_writes=True),
        scope,
    )
    data = await source.async_load()
    previous = await _validate(data)
    if data is not None and any(
        job["status"] not in ("done", "skipped") for job in data["jobs"].values()
    ):
        raise ActivityError("legacy_jobs_need_review")
    destination = ScopedStore(job_store(hass, entry.entry_id), scope)
    current = await _validate(await destination.async_load())
    merged = {**previous.jobs, **current.jobs}
    if len(merged) > CAPACITY:
        raise ActivityError("queue_full")
    await destination.async_save(
        {"jobs": merged, "blocked_until": max(previous.blocked_until, current.blocked_until)}
    )

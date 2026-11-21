"""Agent-assisted legacy receipt/settings adoption, not another setup wizard."""

import re
from copy import deepcopy

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers.storage import Store

from .client import ActivityError
from .const import DOMAIN
from .jobs import CAPACITY, DurableJobs


class _Snapshot:
    def __init__(self, data):
        self.data = data

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)


async def _noop(*args):
    return None


async def async_import_completed_jobs(hass, target_entry_id, expected_athlete_id, receipts):
    """Explicit admin handover after a store-required old-entry removal."""
    from .migration import migration_store

    target = hass.config_entries.async_get_entry(target_entry_id)
    runtime = hass.data.get(DOMAIN, {}).get(target_entry_id)
    if target is None or runtime is None or not target.options.get("preview_only", True):
        raise ActivityError("legacy_target_not_preview")
    source_id = target.data.get("migration_source_entry_id")
    if (
        type(expected_athlete_id) is not str
        or expected_athlete_id != runtime.athlete_id
        or not source_id
    ):
        raise ActivityError("legacy_owner_mismatch")
    proof = await migration_store(hass, source_id).async_load()
    if (
        not isinstance(proof, dict)
        or proof.get("phase") != "active"
        or proof.get("athlete_id") != runtime.athlete_id
        or proof.get("client_id") != runtime.client_id
        or proof.get("source_entry_id") != source_id
        or proof.get("target_entry_id") != target_entry_id
    ):
        raise ActivityError("legacy_owner_mismatch")
    if (
        not isinstance(receipts, dict)
        or len(receipts) > CAPACITY
        or any(
            not isinstance(job, dict) or job.get("status") not in ("done", "skipped")
            for job in receipts.values()
        )
    ):
        raise ActivityError("invalid_terminal_receipts")
    candidate = DurableJobs(
        _Snapshot({"jobs": receipts, "blocked_until": 0}), _noop, _noop, runtime.worker.queue.clock
    )
    await candidate.load()
    await _merge_candidate(runtime.worker.queue, candidate)
    runtime.worker.arm()
    return {"status": "adopted", "jobs": len(candidate.jobs)}


async def _merge_candidate(queue, candidate):
    async with queue._lock:
        merged = deepcopy(queue.jobs)
        for key, job in candidate.jobs.items():
            existing = merged.get(key)
            if existing is None or (
                existing["status"] not in ("done", "skipped")
                and job["status"] in ("done", "skipped")
            ):
                merged[key] = deepcopy(job)
        if len(merged) > CAPACITY:
            raise ActivityError("queue_full")
        await queue._save(merged, max(queue.blocked_until, candidate.blocked_until))


async def async_adopt_activity_tools(hass, target_entry_id, source_entry_id):
    """One-time admin handover; never copy credentials or enable writes."""
    target = hass.config_entries.async_get_entry(target_entry_id)
    source = hass.config_entries.async_get_entry(source_entry_id)
    runtime = hass.data.get(DOMAIN, {}).get(target_entry_id)
    if target is None or runtime is None or not target.options.get("preview_only", True):
        raise ActivityError("legacy_target_not_preview")
    if source is None or source.domain not in ("strava_activity_tools", "activity_tools"):
        raise ActivityError("legacy_source_missing")
    if source.disabled_by is None or source.state is not ConfigEntryState.NOT_LOADED:
        raise ActivityError("legacy_source_active")
    owner = {
        "athlete_id": str(target.data["athlete_id"]),
        "source_entry_id": target.data.get("migration_source_entry_id"),
    }
    if (
        not owner["source_entry_id"]
        or str(source.data.get("athlete_id")) != owner["athlete_id"]
        or source.data.get("source_entry_id") != owner["source_entry_id"]
    ):
        raise ActivityError("legacy_owner_mismatch")
    store = Store(
        hass, 1, f"{source.domain}.{source_entry_id}.jobs", private=True, atomic_writes=True
    )
    data = await store.async_load()
    if data is not None and (not isinstance(data, dict) or data.get("owner") != owner):
        raise ActivityError("legacy_owner_mismatch")
    if (
        data
        and isinstance(data.get("jobs"), dict)
        and any(
            isinstance(job, dict) and job.get("status") == "running"
            for job in data["jobs"].values()
        )
    ):
        raise ActivityError("legacy_jobs_need_review")
    # Validate through the startup loader without mutating the source store.
    candidate = DurableJobs(_Snapshot(data), _noop, _noop, runtime.worker.queue.clock)
    await candidate.load()
    await _merge_candidate(runtime.worker.queue, candidate)
    if not target.options.get("activity_tools_adopted"):
        shoes = source.options.get("track_shoes", "disabled")
        if not isinstance(shoes, str) or not re.fullmatch(r"g[0-9]+|disabled", shoes):
            shoes = "disabled"
        hass.config_entries.async_update_entry(
            target,
            options={
                **target.options,
                "track_shoes": shoes,
                "preview_only": True,
                "activity_tools_adopted": True,
            },
        )
    runtime.worker.arm()
    return {"status": "adopted", "jobs": len(candidate.jobs)}

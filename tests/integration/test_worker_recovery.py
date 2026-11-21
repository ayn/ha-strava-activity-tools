"""Native storage, timers and account options govern durable recovery."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.ha_strava_automation.client import ActivityError
from custom_components.ha_strava_automation.const import DOMAIN
from custom_components.ha_strava_automation.worker import ScopedStore, job_store

from . import test_features

standalone = test_features.standalone


async def test_storage_failure_stops_before_external_work(hass, standalone):
    entry, _ = standalone
    hass.config_entries.async_update_entry(entry, options={**entry.options, "preview_only": False})
    runtime = entry.runtime_data
    with (
        patch.object(runtime.worker.queue.store, "async_save", AsyncMock(side_effect=OSError())),
        patch.object(runtime.client, "get", AsyncMock()) as get,
    ):
        with pytest.raises(HomeAssistantError, match="job_storage_unavailable"):
            await hass.services.async_call(
                DOMAIN,
                "mute_activity",
                {
                    "config_entry_id": entry.entry_id,
                    "activity_id": "123",
                    "filters": {"name_contains": "Synthetic"},
                    "dry_run": False,
                },
                blocking=True,
                return_response=True,
            )
        assert runtime.worker.closed
        assert not runtime.worker.queue.jobs
        get.assert_not_awaited()


async def test_native_restart_retains_pending_retry_without_polling(hass, standalone, freezer):
    entry, _ = standalone
    runtime = entry.runtime_data
    with patch.object(
        runtime.client, "get", AsyncMock(side_effect=ActivityError("upstream_unavailable"))
    ):
        result = await runtime.worker.submit("fetch", "123")
    assert result["status"] == "queued"
    assert await hass.config_entries.async_reload(entry.entry_id)
    worker = entry.runtime_data.worker
    assert worker.queue.jobs[result["job_id"]]["attempt"] == 1
    assert worker.queue.jobs[result["job_id"]]["status"] == "queued"
    freezer.tick(61)
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert worker.queue.jobs[result["job_id"]]["status"] == "done"
    with patch.object(entry.runtime_data.client, "get", AsyncMock()) as get:
        freezer.tick(600)
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done(wait_background_tasks=True)
        get.assert_not_awaited()


async def test_store_owner_cannot_be_changed_to_replay_another_athletes_receipts(hass, standalone):
    entry, _ = standalone
    await entry.runtime_data.worker.submit("fetch", "123")
    foreign = ScopedStore(job_store(hass, entry.entry_id), {"client_id": "100", "athlete_id": "99"})
    with pytest.raises(ActivityError, match="job_owner_mismatch"):
        await foreign.async_load()


async def test_enabling_writes_replays_only_preview_blocked_jobs(hass, standalone):
    entry, _ = standalone
    worker = entry.runtime_data.worker
    blocked = await worker.submit("update", "123", {"changes": {"commute": True}, "dry_run": False})
    assert worker.queue.jobs[blocked["job_id"]]["last_error"] == "writes_disabled"
    with patch.object(
        worker.queue, "execute", AsyncMock(side_effect=ActivityError("reauth_required"))
    ):
        auth = await worker.submit("fetch", "124")
    hass.config_entries.async_update_entry(entry, options={**entry.options, "preview_only": False})
    await hass.async_block_till_done()
    assert worker.queue.jobs[blocked["job_id"]]["status"] == "queued"
    assert worker.queue.jobs[auth["job_id"]]["status"] == "failed"
    assert worker.queue.jobs[auth["job_id"]]["last_error"] == "reauth_required"

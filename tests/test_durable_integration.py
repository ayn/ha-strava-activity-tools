"""Real HA storage and service tests for restart recovery and manual repair."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.strava_activity_tools import (
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.strava_activity_tools.client import ActivityError
from custom_components.strava_activity_tools.const import DOMAIN, READY_EVENT, SOURCE_EVENT
from custom_components.strava_activity_tools.worker import JobWorker, ScopedStore, job_store


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    pass


@pytest.fixture
async def configured(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="42",
        entry_id="tools",
        data={"source_entry_id": "source", "athlete_id": "42"},
        options={"preview_only": False},
    )
    entry.add_to_hass(hass)
    hass.data["ha_strava"] = {"source": SimpleNamespace(oauth_session=AsyncMock())}
    await async_setup(hass, {})
    hass.config.components.add(DOMAIN)
    async with entry.setup_lock:
        await async_setup_entry(hass, entry)
    hass.states.async_set("zone.home", "0", {"latitude": 0, "longitude": 0})
    activity = {
        "id": 9,
        "athlete": {"id": 42},
        "sport_type": "Run",
        "description": "notes",
        "start_date": (dt_util.utcnow() - timedelta(hours=1)).isoformat(),
        "start_latlng": [0, 0],
    }
    return entry, hass.data[DOMAIN]["tools"], activity


async def call(hass, service="append_environment", **changes):
    data = {
        "config_entry_id": "tools",
        "activity_id": "9",
        "aqi_entity": "sensor.aqi",
        "uv_entity": "sensor.uv",
        "dry_run": False,
    }
    data.update(changes)
    return await hass.services.async_call(
        DOMAIN, service, data, blocking=True, return_response=True
    )


async def test_actual_store_committed_before_fetch_and_duplicate_webhook(hass, configured):
    entry, runtime, activity = configured
    events = async_capture_events(hass, READY_EVENT)

    async def fetch(_):
        stored = await job_store(hass, "tools").async_load()
        assert stored["owner"]["athlete_id"] == "42"
        assert list(stored["jobs"].values())[0]["status"] == "running"
        return activity

    with patch.object(runtime.client, "get", AsyncMock(side_effect=fetch)) as get:
        hass.bus.async_fire(SOURCE_EVENT, {"athlete_id": "42", "activity_id": "9"})
        await hass.async_block_till_done(wait_background_tasks=True)
        hass.bus.async_fire(SOURCE_EVENT, {"athlete_id": "42", "activity_id": "9"})
        await hass.async_block_till_done(wait_background_tasks=True)
        assert len(events) == 1
        assert get.await_count == 1
    await async_unload_entry(hass, entry)


async def test_environment_restart_resumes_without_polling(hass, configured, freezer):
    entry, runtime, activity = configured
    with patch.object(
        runtime.client, "get", AsyncMock(side_effect=ActivityError("upstream_unavailable"))
    ):
        queued = await call(hass)
    assert queued["status"] == "queued"
    key = queued["job_id"]
    await runtime.worker.close()
    restarted = JobWorker(hass, entry, AsyncMock(return_value={"status": "unchanged"}))
    await restarted.load()
    assert key in restarted.queue.jobs
    await restarted.process()
    assert not restarted.queue.execute.called
    freezer.tick(61)
    await restarted.process()
    assert restarted.queue.execute.await_count == 1
    assert restarted.queue.jobs[key]["status"] == "done"
    await restarted.close()


async def test_environment_preview_creates_no_durable_write_intent(hass, configured):
    entry, runtime, activity = configured
    with (
        patch.object(runtime.client, "get", AsyncMock(return_value=activity)),
        patch(
            "custom_components.strava_activity_tools.async_readings",
            AsyncMock(return_value=[35, 0]),
        ),
        patch(
            "custom_components.strava_activity_tools.enrich_description",
            AsyncMock(return_value={"status": "would_update"}),
        ),
    ):
        result = await call(hass, dry_run=True)
    assert result["status"] == "would_update"
    assert runtime.worker.queue.jobs == {}
    await runtime.worker.close()


async def test_manual_replay_and_status_work_when_source_is_offline(hass, configured):
    entry, runtime, _ = configured
    execute = AsyncMock(side_effect=ActivityError("reauth_required"))
    with patch.object(runtime.worker.queue, "execute", execute):
        failed = await runtime.worker.submit("fetch", "9")
    key = failed["job_id"]
    hass.data["ha_strava"].clear()
    status = await hass.services.async_call(
        DOMAIN, "list_jobs", {"config_entry_id": "tools"}, blocking=True, return_response=True
    )
    assert status["jobs"][0]["last_error"] == "reauth_required"
    result = await hass.services.async_call(
        DOMAIN,
        "replay_job",
        {
            "config_entry_id": "tools",
            "job_id": key,
        },
        blocking=True,
        return_response=True,
    )
    assert result["status"] == "queued"
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "replay_job",
            {
                "config_entry_id": "someone_else",
                "job_id": key,
            },
            blocking=True,
            return_response=True,
        )
    await runtime.worker.close()


async def test_store_owner_cannot_change_on_reload(hass, configured):
    entry, runtime, _ = configured
    with patch.object(
        runtime.worker.queue, "execute", AsyncMock(return_value={"status": "updated"})
    ):
        await runtime.worker.submit("fetch", "9")
    changed = ScopedStore(
        job_store(hass, "tools"), {"source_entry_id": "source", "athlete_id": "99"}
    )
    with pytest.raises(ActivityError, match="job_owner_mismatch"):
        await changed.async_load()
    await runtime.worker.close()


async def test_source_offline_does_not_lose_environment_intent(hass, configured):
    entry, runtime, _ = configured
    hass.data["ha_strava"].clear()
    result = await call(hass)
    assert result["status"] == "queued"
    assert runtime.worker.queue.jobs[result["job_id"]]["last_error"] == "source_not_loaded"
    await runtime.worker.close()


async def test_retry_timer_runs_only_due_job_and_is_removed_on_unload(hass, configured, freezer):
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    entry, runtime, activity = configured
    execute = AsyncMock(
        side_effect=[ActivityError("upstream_unavailable"), {"status": "unchanged"}]
    )
    with patch.object(runtime.worker.queue, "execute", execute):
        await runtime.worker.submit("fetch", "9")
        freezer.tick(61)
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done(wait_background_tasks=True)
        assert execute.await_count == 2
        await runtime.worker.process()
        assert execute.await_count == 2
    await async_unload_entry(hass, entry)
    assert runtime.worker.closed
    assert runtime.worker._cancel_timer is None


async def test_submission_storage_failure_stops_worker_without_external_calls(hass, configured):
    entry, runtime, _ = configured
    with (
        patch.object(
            runtime.worker.queue.store,
            "async_save",
            AsyncMock(side_effect=OSError("private detail")),
        ),
        patch.object(runtime.client, "get", AsyncMock()) as get,
    ):
        with pytest.raises(HomeAssistantError, match="job_storage_unavailable"):
            await call(hass)
        assert runtime.worker.closed
        get.assert_not_called()
        assert not runtime.worker.queue.jobs
    await runtime.worker.close()


async def test_preview_lock_overrides_write_mode_and_reports_readings(hass, configured):
    entry, runtime, activity = configured
    hass.config_entries.async_update_entry(entry, options={"preview_only": True})
    with (
        patch.object(runtime.client, "get", AsyncMock(return_value=activity)),
        patch(
            "custom_components.strava_activity_tools.async_readings",
            AsyncMock(return_value=[35, 0]),
        ),
        patch(
            "custom_components.strava_activity_tools.enrich_description",
            AsyncMock(return_value={"status": "would_update"}),
        ) as enrich,
    ):
        result = await call(hass, dry_run=False)
    enrich.assert_awaited_once_with(runtime.client, "9", 35, 0, True)
    assert result["aqi"] == 35
    assert result["uv"] == 0
    assert result["start_time"] == activity["start_date"]
    assert not runtime.worker.queue.jobs
    await runtime.worker.close()


async def test_preview_lock_blocks_generic_write_action(hass, configured):
    entry, runtime, _ = configured
    hass.config_entries.async_update_entry(entry, options={"preview_only": True})
    with patch.object(
        runtime.client, "update", AsyncMock(return_value={"status": "would_update"})
    ) as update:
        await hass.services.async_call(
            DOMAIN,
            "update_activity",
            {
                "config_entry_id": "tools",
                "activity_id": "9",
                "changes": {"name": "new"},
                "dry_run": False,
            },
            blocking=True,
            return_response=True,
        )
    update.assert_awaited_once_with("9", {"name": "new"}, True)
    await runtime.worker.close()


async def test_pending_real_write_cannot_resume_through_preview_lock(hass, configured):
    entry, runtime, _ = configured
    hass.config_entries.async_update_entry(entry, options={"preview_only": True})
    with patch.object(runtime.client, "get", AsyncMock()) as get:
        result = await runtime.worker.submit(
            "environment",
            "9",
            {
                "aqi_entity": "sensor.aqi",
                "uv_entity": "sensor.uv",
                "radius_km": 15,
                "dry_run": False,
            },
        )
        get.assert_not_called()
    assert result["status"] == "failed"
    assert runtime.worker.queue.jobs[result["job_id"]]["last_error"] == "writes_disabled"
    await runtime.worker.close()


async def test_mute_service_retains_live_intent_across_reload_and_preview_lock(
    hass, configured, freezer
):
    entry, runtime, activity = configured
    activity.update(name="Tracker walk", sport_type="Walk", hide_from_home=False)
    source = hass.data["ha_strava"].pop("source")
    result = await hass.services.async_call(
        DOMAIN,
        "mute_activity",
        {
            "config_entry_id": "tools",
            "activity_id": "9",
            "filters": {"name_contains": "Tracker"},
            "dry_run": False,
        },
        blocking=True,
        return_response=True,
    )
    key = result["job_id"]
    assert result["status"] == "queued"
    stored = await job_store(hass, "tools").async_load()
    assert stored["jobs"][key]["parameters"]["filters"] == {"name_contains": "Tracker"}
    await async_unload_entry(hass, entry)
    hass.data["ha_strava"]["source"] = source
    # An operator turns previews back on while a saved live job is waiting.
    hass.config_entries.async_update_entry(entry, options={"preview_only": True})
    async with entry.setup_lock:
        await async_setup_entry(hass, entry)
    runtime = hass.data[DOMAIN]["tools"]
    freezer.tick(timedelta(seconds=61))
    with patch.object(runtime.client, "get", AsyncMock(return_value=activity)) as get:
        await runtime.worker.process()
    get.assert_not_called()
    assert runtime.worker.queue.jobs[key]["status"] == "failed"
    assert runtime.worker.queue.jobs[key]["last_error"] == "writes_disabled"
    await async_unload_entry(hass, entry)


async def test_mute_live_intent_survives_reload_and_rechecks_without_new_event(
    hass, configured, freezer
):
    entry, runtime, activity = configured
    activity.update(name="Tracker walk", sport_type="Walk", hide_from_home=False)
    source = hass.data["ha_strava"].pop("source")
    result = await hass.services.async_call(
        DOMAIN,
        "mute_activity",
        {
            "config_entry_id": "tools",
            "activity_id": "9",
            "filters": {"name_contains": "Tracker"},
            "dry_run": False,
        },
        blocking=True,
        return_response=True,
    )
    await async_unload_entry(hass, entry)
    hass.data["ha_strava"]["source"] = source
    async with entry.setup_lock:
        await async_setup_entry(hass, entry)
    runtime = hass.data[DOMAIN]["tools"]
    freezer.tick(timedelta(seconds=61))
    with patch.object(
        runtime.client, "transform", AsyncMock(return_value={"status": "unchanged", "changes": {}})
    ) as transform:
        await runtime.worker.process()
    transform.assert_awaited_once()
    assert runtime.worker.queue.jobs[result["job_id"]]["status"] == "done"
    assert runtime.journal.recent(False)[0]["operation"] == "mute_activity"
    await async_unload_entry(hass, entry)

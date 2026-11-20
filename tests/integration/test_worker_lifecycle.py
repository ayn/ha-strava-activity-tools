"""Persisted operations must not run before account setup has fully succeeded."""

from unittest.mock import AsyncMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_strava_automation.const import DOMAIN
from custom_components.ha_strava_automation.worker import JobWorker


async def test_pending_job_waits_for_successful_setup_before_execution(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={"client_id": "100", "athlete_id": "42"})
    entry.add_to_hass(hass)
    execute = AsyncMock(return_value={"status": "unchanged"})
    worker = JobWorker(hass, entry, execute)
    worker.ready = False
    try:
        await worker.load()
        key = await worker.queue.submit(
            "update", "123", {"changes": {"commute": True}, "dry_run": False}
        )
        await worker.process()
        execute.assert_not_awaited()
        assert worker.queue.jobs[key]["status"] == "queued"
        worker.resume()
        await worker.process()
        execute.assert_awaited_once()
        assert worker.queue.jobs[key]["status"] == "done"
    finally:
        await worker.close()


async def test_failed_native_setup_keeps_saved_writes_and_cleans_runtime(
    hass, oauth_app, oauth_responses, freezer, hass_client_no_auth
):
    from unittest.mock import patch

    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    from custom_components.ha_strava_automation.client import ActivityError

    from .test_auth import account

    entry = account(hass, oauth_app)
    hass.config_entries.async_update_entry(
        entry, options={"preview_only": False, "webhook_enabled": True}
    )
    seed = JobWorker(hass, entry, AsyncMock(), start_paused=True)
    key = await seed.queue.submit("update", "123", {"changes": {"commute": True}, "dry_run": False})
    await seed.close()
    oauth_responses()
    with patch(
        "custom_components.ha_strava_automation.subscriptions.SubscriptionManager.connect",
        AsyncMock(side_effect=ActivityError("subscription_conflict")),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.runtime_data is None
    assert not hass.data[DOMAIN].get(entry.entry_id)
    # The saved operation must still be queued after setup cleanup and timer advance.
    check = JobWorker(hass, entry, AsyncMock(), start_paused=True)
    try:
        await check.load()
        assert check.queue.jobs[key]["status"] == "queued"
        freezer.tick(1)
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done(wait_background_tasks=True)
        check.queue.execute.assert_not_awaited()
        assert all(not app.targets for app in hass.data[f"{DOMAIN}_applications"].values())
    finally:
        await check.close()


async def test_disabled_entry_cannot_execute_a_saved_live_intent(
    hass, oauth_app, oauth_responses, aioclient_mock
):
    from homeassistant.config_entries import ConfigEntryDisabler

    from .test_auth import account

    entry = account(hass, oauth_app)
    hass.config_entries.async_update_entry(
        entry, options={"preview_only": False, "webhook_enabled": False}
    )
    oauth_responses()
    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime = entry.runtime_data
    aioclient_mock.get(
        "https://www.strava.com/api/v3/activities/123",
        json={"id": 123, "athlete": {"id": 42}, "sport_type": "Run", "commute": True},
    )
    before = aioclient_mock.call_count
    # Exercise the interval between disable and native unload completion.
    entry.disabled_by = ConfigEntryDisabler.USER
    try:
        result = await runtime.worker.submit(
            "update", "123", {"changes": {"commute": True}, "dry_run": False}
        )
        assert result["status"] == "queued"
        assert runtime.worker.queue.jobs[result["job_id"]]["last_error"] == "source_not_loaded"
        assert aioclient_mock.call_count == before
    finally:
        entry.disabled_by = None

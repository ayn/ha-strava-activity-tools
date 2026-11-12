"""Durable state-machine contracts, without sleeping or contacting providers."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from custom_components.strava_activity_tools.client import ActivityError
from custom_components.strava_activity_tools.jobs import DurableJobs


class Disk:
    """A fake durable device: new queue instances share only committed bytes."""

    def __init__(self):
        self.data = None
        self.fail = False

    async def async_save(self, value):
        if self.fail:
            raise OSError("disk unavailable")
        self.data = deepcopy(value)

    async def async_load(self):
        return deepcopy(self.data)


async def test_weather_retry_survives_reload_without_blocking_strava_fetches(setup):
    _, execute, _, now, make = setup
    execute.side_effect = [ActivityError("weather_unavailable", 120), {"status": "delivered"}]
    queue = make()
    weather = await queue.submit(
        "environment",
        "9",
        {
            "aqi_entity": "sensor.aqi",
            "uv_entity": "sensor.uv",
            "external_fallback": True,
            "dry_run": False,
        },
    )
    fetch = await queue.submit("fetch", "10")
    await queue.run_due()
    assert queue.jobs[weather]["status"] == "queued"
    assert queue.jobs[weather]["next_retry"] == now[0] + 120
    assert queue.jobs[fetch]["status"] == "done"
    assert queue.blocked_until == 0
    loaded = make()
    await loaded.load()
    assert loaded.jobs[weather]["parameters"]["external_fallback"] is True
    now[0] += 121
    execute.side_effect = None
    execute.return_value = {"status": "updated"}
    await loaded.run_due()
    assert loaded.jobs[weather]["status"] == "done"


@pytest.mark.parametrize("flag", [1, "true", None])
async def test_external_fallback_flag_is_explicit_boolean(setup, flag):
    *_, make = setup
    with pytest.raises(ActivityError, match="invalid_job"):
        await make().submit(
            "environment",
            "9",
            {"aqi_entity": "sensor.aqi", "uv_entity": "sensor.uv", "external_fallback": flag},
        )


async def test_api_only_job_reloads_without_home_or_sensor_settings(setup):
    _, execute, _, _, make = setup
    queue = make()
    key = await queue.submit(
        "environment", "9", {"weather_source": "location_api", "dry_run": False}
    )
    restarted = make()
    await restarted.load()
    assert restarted.jobs[key]["parameters"] == {"weather_source": "location_api", "dry_run": False}
    await restarted.run_due()
    execute.assert_awaited_once()
    assert restarted.jobs[key]["status"] == "done"


@pytest.fixture
def setup():
    disk, execute, notify, now = (
        Disk(),
        AsyncMock(return_value={"status": "updated"}),
        AsyncMock(),
        [1000.0],
    )

    def make():
        return DurableJobs(disk, execute, notify, lambda: now[0])

    return disk, execute, notify, now, make


async def test_commit_intent_before_side_effect_and_survive_restart(setup):
    disk, execute, _, _, make = setup
    first = make()
    key = await first.submit(
        "environment",
        "9",
        {"aqi_entity": "sensor.aqi", "uv_entity": "sensor.uv", "radius_km": 15, "dry_run": False},
    )
    assert not execute.called
    second = make()
    await second.load()
    assert key in second.jobs
    await second.run_due()
    assert second.jobs[key]["status"] == "done"
    assert execute.await_count == 1
    assert disk.data is not None


async def test_duplicate_delivery_and_completed_receipt_survive_restart(setup):
    _, execute, _, _, make = setup
    queue = make()
    key = await queue.submit("fetch", "9")
    assert await queue.submit("fetch", "9") == key
    await queue.run_due()
    restarted = make()
    await restarted.load()
    assert await restarted.submit("fetch", "9") == key
    await restarted.run_due()
    assert execute.await_count == 1


async def test_rate_limit_survives_restart_and_blocks_other_due_jobs(setup):
    _, execute, _, now, make = setup
    execute.side_effect = [
        ActivityError("rate_limited", 900),
        {"status": "updated"},
        {"status": "updated"},
    ]
    queue = make()
    await queue.submit("fetch", "9")
    await queue.submit("fetch", "10")
    await queue.run_due()
    assert execute.await_count == 1
    restarted = make()
    await restarted.load()
    await restarted.run_due()
    assert execute.await_count == 1
    now[0] += 900
    await restarted.run_due()
    assert execute.await_count == 3


async def test_crash_after_possible_write_rechecks_before_repeating(setup):
    disk, execute, _, _, make = setup
    queue = make()
    key = await queue.submit("fetch", "9")
    import asyncio

    execute.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await queue.run_due()
    assert disk.data["jobs"][key]["status"] == "running"
    restarted = make()
    await restarted.load()
    execute.side_effect = None
    execute.return_value = {
        "status": "unchanged",
        "description": "private notes must not be stored",
    }
    await restarted.run_due()
    assert restarted.jobs[key]["status"] == "done"
    assert "private notes" not in str(disk.data)


async def test_auth_failure_stops_until_manual_replay_with_one_recovery(setup):
    _, execute, notify, _, make = setup
    execute.side_effect = ActivityError("reauth_required")
    queue = make()
    key = await queue.submit("fetch", "9")
    await queue.run_due()
    assert queue.jobs[key]["status"] == "failed"
    assert notify.await_count == 1
    await queue.run_due()
    assert execute.await_count == 1
    restarted = make()
    await restarted.load()
    await restarted.replay(key)
    execute.side_effect = None
    await restarted.run_due()
    assert notify.await_count == 2
    assert notify.call_args.args[1] == "recovered"


async def test_transient_backoff_is_bounded_and_notifications_deduplicate(setup):
    _, execute, notify, now, make = setup
    execute.side_effect = ActivityError("upstream_unavailable")
    queue = make()
    key = await queue.submit("fetch", "9")
    for _ in range(8):
        await queue.run_due()
        now[0] += 20000
    assert execute.await_count == 5
    assert queue.jobs[key]["status"] == "failed"
    assert notify.await_count == 1


async def test_disk_failure_prevents_any_external_call(setup):
    disk, execute, _, _, make = setup
    disk.fail = True
    queue = make()
    with pytest.raises(OSError):
        await queue.submit("fetch", "9")
    assert not execute.called
    assert queue.jobs == {}


@pytest.mark.parametrize(
    ("kind", "activity_id", "params"),
    [
        ("fetch", "../9", {}),
        ("fetch", "٩", {}),
        ("fetch", True, {}),
        ("fetch", "0", {}),
        ("arbitrary_api", "9", {}),
        ("environment", "9", {"description": "private"}),
        (
            "environment",
            "9",
            {"aqi_entity": "sensor.aqi", "uv_entity": "sensor.uv", "dry_run": "false"},
        ),
    ],
)
async def test_invalid_intents_cannot_be_saved_or_executed(setup, kind, activity_id, params):
    disk, execute, _, _, make = setup
    with pytest.raises(ActivityError):
        await make().submit(kind, activity_id, params)
    assert disk.data is None
    assert not execute.called


async def test_prune_after_seven_days_and_refuse_expired_manual_replay(setup):
    disk, execute, _, now, make = setup
    queue = make()
    key = await queue.submit("fetch", "9")
    now[0] += 7 * 86400 + 1
    await queue.run_due()
    assert key not in queue.jobs
    assert not execute.called
    with pytest.raises(ActivityError):
        await queue.replay(key)
    assert key not in disk.data["jobs"]


async def test_changed_parameters_are_a_distinct_intent_and_input_is_not_mutable(setup):
    _, _, _, _, make = setup
    queue = make()
    params = {
        "aqi_entity": "sensor.aqi",
        "uv_entity": "sensor.uv",
        "radius_km": 15,
        "dry_run": True,
    }
    first = await queue.submit("environment", "9", params)
    params["dry_run"] = False
    second = await queue.submit("environment", "9", params)
    assert first != second
    assert queue.jobs[first]["parameters"]["dry_run"] is True


@pytest.mark.parametrize(
    "change",
    [
        {"created_at": "bad"},
        {"attempt": -2},
        {"notified": "false"},
        {"last_error": "raw private provider body"},
        {"description": "private notes"},
    ],
)
async def test_corrupt_store_metadata_fails_closed(setup, change):
    disk, execute, _, _, make = setup
    queue = make()
    key = await queue.submit("fetch", "9")
    disk.data["jobs"][key].update(change)
    with pytest.raises(ActivityError, match="invalid_job_store"):
        await make().load()
    assert not execute.called


async def test_crash_on_last_attempt_remains_reloadable(setup):
    import asyncio

    disk, execute, _, _, make = setup
    queue = make()
    key = await queue.submit("fetch", "9")
    disk.data["jobs"][key]["attempt"] = 4
    queue = make()
    await queue.load()
    execute.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await queue.run_due()
    restarted = make()
    await restarted.load()
    execute.side_effect = ActivityError("upstream_unavailable")
    await restarted.run_due()
    again = make()
    await again.load()
    assert again.jobs[key]["status"] == "failed"


async def test_expiry_dismisses_incident_without_claiming_recovery(setup):
    _, execute, notify, now, make = setup
    execute.side_effect = ActivityError("reauth_required")
    queue = make()
    key = await queue.submit("fetch", "9")
    await queue.run_due()
    now[0] += 7 * 86400 + 1
    await queue.run_due()
    assert notify.call_args.args == (key, "expired", "")
    assert key not in queue.jobs


async def test_ineligible_retry_is_skipped_not_reported_as_verified_recovery(setup):
    _, execute, notify, _, make = setup
    queue = make()
    key = await queue.submit("fetch", "9")
    execute.side_effect = ActivityError("reauth_required")
    await queue.run_due()
    await queue.replay(key)
    execute.side_effect = None
    execute.return_value = {"status": "skipped_ineligible"}
    await queue.run_due()
    assert queue.jobs[key]["status"] == "skipped"
    assert notify.call_args.args == (key, "expired", "")


async def test_mute_intent_retains_filters_across_restart_and_source_recovery(setup):
    disk, execute, _, now, make = setup
    queue = make()
    params = {"filters": {"name_contains": "Tracker walk"}, "dry_run": False}
    key = await queue.submit("mute", "9", params)
    assert disk.data["jobs"][key]["parameters"] == params
    execute.side_effect = ActivityError("source_not_loaded")
    for _ in range(7):
        await queue.run_due()
        now[0] += 7201
    assert queue.jobs[key]["status"] == "queued"
    loaded = make()
    await loaded.load()
    execute.side_effect = None
    await loaded.run_due()
    assert loaded.jobs[key]["status"] == "done"


@pytest.mark.parametrize(
    "filters",
    [
        {},
        {"name_contains": " "},
        {"name_contains": 3},
        {"unknown": "anything"},
        {"sport_types": "Run"},
        {"distance_below_miles": True},
        {"distance_below_miles": float("nan")},
        {"distance_below_miles": 0},
    ],
)
async def test_invalid_mute_filters_cannot_create_intents(setup, filters):
    disk, execute, _, _, make = setup
    with pytest.raises(ActivityError):
        await make().submit("mute", "9", {"filters": filters, "dry_run": False})
    assert disk.data is None
    execute.assert_not_called()


async def test_workout_exemption_survives_durable_mute_reload(setup):
    disk, execute, _, _, make = setup
    queue = make()
    params = {
        "filters": {"sport_types": ["Run"], "distance_below_miles": 2.1, "exclude_workouts": True},
        "dry_run": False,
    }
    key = await queue.submit("mute", "9", params)
    loaded = make()
    await loaded.load()
    assert loaded.jobs[key]["parameters"] == params
    execute.return_value = {"status": "skipped_ineligible"}
    await loaded.run_due()
    assert loaded.jobs[key]["status"] == "skipped"

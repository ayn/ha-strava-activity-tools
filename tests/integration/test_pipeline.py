"""Native HA HTTP ingress -> durable inbox/worker -> owned API -> automation."""

from copy import deepcopy
from datetime import timedelta

import pytest
from aiohttp import web
from homeassistant.core import SupportsResponse
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.ha_strava_automation.const import DOMAIN, READY_EVENT, WORKOUT_EVENT

from .test_auth import account


@pytest.fixture
async def pipeline(
    hass, oauth_app, aiohttp_server, socket_enabled, monkeypatch, hass_client_no_auth
):
    activity = {
        "id": 123,
        "athlete": {"id": 42},
        "sport_type": "Run",
        "name": "Synthetic run",
        "distance": 1609.344,
        "workout_type": 0,
        "gear_id": None,
        "trainer": False,
        "manual": False,
        "description": "Keep these independent notes",
        "start_date": dt_util.utcnow().isoformat(),
        "timezone": "UTC",
        "laps": [{"distance": 400, "moving_time": 80 if i % 2 == 0 else 140} for i in range(8)],
    }
    remote, writes, requests = [], [], []

    async def api(request):
        if request.path.startswith("/subscriptions"):
            form = request.query if request.method in ("GET", "DELETE") else await request.post()
            assert form["client_id"] == "100"
            if request.method == "GET":
                return web.json_response(remote)
            if request.method == "POST":
                remote.append({"id": 12, "callback_url": form["callback_url"]})
                return web.json_response({"id": 12}, status=201)
            return web.Response(status=204)
        assert request.headers["Authorization"] == "Bearer old-access"
        requests.append(request.path)
        if request.path == "/athlete":
            return web.json_response({"id": 42, "shoes": []})
        value = deepcopy(activity)
        value["id"] = int(request.match_info["id"])
        if value["id"] == 999:
            value["athlete"]["id"] = 99
            value["name"] = "must not be disclosed"
        if request.method == "PUT":
            fields = dict(await request.post())
            writes.append(fields)
            for key, val in fields.items():
                activity[key] = (
                    int(val)
                    if key == "workout_type"
                    else val == "true"
                    if val in ("true", "false")
                    else val
                )
            value = deepcopy(activity)
        return web.json_response(value)

    app = web.Application()
    app.router.add_route("*", "/subscriptions", api)
    app.router.add_delete("/subscriptions/{id}", api)
    app.router.add_get("/athlete", api)
    app.router.add_route("*", "/activities/{id}", api)
    server = await aiohttp_server(app)
    base = str(server.make_url("/")).rstrip("/")
    monkeypatch.setattr(
        "custom_components.ha_strava_automation.auth.ATHLETE_URL", base + "/athlete"
    )
    monkeypatch.setattr("custom_components.ha_strava_automation.client.BASE_URL", base)
    monkeypatch.setattr(
        "custom_components.ha_strava_automation.subscriptions.SUBSCRIPTIONS_URL",
        base + "/subscriptions",
    )
    hass.config.external_url = "https://ha.example.test"
    entry = account(hass, oauth_app)
    hass.config_entries.async_update_entry(
        entry, options={"preview_only": False, "webhook_enabled": True}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    client = await hass_client_no_auth()
    hook = entry.runtime_data.application.record["webhook_id"]
    yield entry, client, f"/api/webhook/{hook}", activity, writes, requests
    await hass.config_entries.async_unload(entry.entry_id)


async def tick(hass, freezer):
    freezer.tick(1)
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)


def delivery(identifier=123):
    return {
        "subscription_id": 12,
        "owner_id": 42,
        "object_id": identifier,
        "object_type": "activity",
        "aspect_type": "create",
        "event_time": int(dt_util.utcnow().timestamp()),
    }


async def test_durable_ingress_verifies_api_owner_and_suppresses_duplicates(
    hass, pipeline, freezer
):
    entry, client, path, _, writes, _ = pipeline
    events = async_capture_events(hass, READY_EVENT)
    assert (await client.post(path, json=delivery())).status == 200
    await tick(hass, freezer)
    await tick(hass, freezer)
    assert len(events) == 1
    assert events[0].data["config_entry_id"] == entry.entry_id
    assert "description" not in events[0].data
    assert "start_latlng" not in events[0].data
    assert (await client.post(path, json=delivery())).status == 200
    await tick(hass, freezer)
    assert len(events) == 1
    assert not writes
    assert (await client.post(path, json=delivery(999))).status == 200
    await tick(hass, freezer)
    await tick(hass, freezer)
    assert len(events) == 1
    assert all(
        "must not be disclosed" not in str(row) for row in entry.runtime_data.journal.records
    )
    assert any(
        job["last_error"] == "wrong_athlete"
        for job in entry.runtime_data.worker.queue.jobs.values()
    )


async def test_native_automation_tags_before_short_mute_and_preserves_notes(
    hass, pipeline, freezer
):
    entry, client, path, activity, writes, _ = pipeline

    async def calendar(_call):
        start = dt_util.parse_datetime(activity["start_date"])
        today = start.date().isoformat()
        tomorrow = (start + timedelta(days=1)).date().isoformat()
        return {
            "calendar.training": {
                "events": [{"start": today, "end": tomorrow, "summary": "4 x 400m"}]
            }
        }

    hass.services.async_register(
        "calendar", "get_events", calendar, supports_response=SupportsResponse.ONLY
    )
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": [
                {
                    "alias": "Synthetic workout tagging",
                    "triggers": [
                        {
                            "trigger": "event",
                            "event_type": READY_EVENT,
                            "event_data": {"config_entry_id": entry.entry_id},
                        }
                    ],
                    "actions": [
                        {
                            "action": f"{DOMAIN}.process_workout",
                            "data": {
                                "config_entry_id": entry.entry_id,
                                "activity_id": "{{ trigger.event.data.activity_id }}",
                                "calendar_entity": "calendar.training",
                                "dry_run": False,
                            },
                        }
                    ],
                },
                {
                    "alias": "Synthetic short mute",
                    "triggers": [
                        {
                            "trigger": "event",
                            "event_type": WORKOUT_EVENT,
                            "event_data": {"config_entry_id": entry.entry_id},
                        }
                    ],
                    "actions": [
                        {
                            "action": f"{DOMAIN}.mute_activity",
                            "data": {
                                "config_entry_id": entry.entry_id,
                                "activity_id": "{{ trigger.event.data.activity_id }}",
                                "filters": {"distance_below_miles": 2.1, "exclude_workouts": True},
                                "dry_run": False,
                            },
                        }
                    ],
                },
            ]
        },
    )
    processed = async_capture_events(hass, WORKOUT_EVENT)
    assert (await client.post(path, json=delivery())).status == 200
    for _ in range(5):
        await tick(hass, freezer)
    assert activity["workout_type"] == 3
    assert writes == [{"workout_type": "3"}]
    assert activity["description"] == "Keep these independent notes"
    assert len(processed) == 1
    assert not activity.get("hide_from_home", False)


async def test_successful_update_recovery_does_not_overwrite_new_manual_edit(
    hass, pipeline, freezer
):
    entry, _, _, activity, writes, _ = pipeline
    params = {"changes": {"workout_type": 3}, "expected": {"workout_type": 0}, "dry_run": False}
    key = await entry.runtime_data.worker.queue.submit("update", "123", params)
    # Simulate crash after the provider committed but before the local receipt.
    activity["workout_type"] = 3
    await entry.runtime_data.worker.process()
    assert entry.runtime_data.worker.queue.jobs[key]["status"] == "done"
    assert writes == []
    # A later manual edit must not be reversed by duplicate intent submission.
    activity["workout_type"] = 1
    await entry.runtime_data.worker.submit("update", "123", params)
    assert activity["workout_type"] == 1
    assert writes == []


@pytest.mark.freeze_time("2026-02-10T23:59:30+00:00")
async def test_calendar_outage_retries_saved_workout_without_another_webhook(
    hass, pipeline, freezer
):
    from homeassistant.exceptions import HomeAssistantError

    entry, _, _, activity, writes, _ = pipeline
    available = False

    async def calendar(_call):
        if not available:
            raise HomeAssistantError("Synthetic calendar unavailable")
        today = dt_util.parse_datetime(activity["start_date"])
        return {
            "calendar.training": {
                "events": [
                    {
                        "start": today.date().isoformat(),
                        "end": (today + timedelta(days=1)).date().isoformat(),
                        "summary": "4 x 400m",
                    }
                ]
            }
        }

    hass.services.async_register(
        "calendar", "get_events", calendar, supports_response=SupportsResponse.ONLY
    )
    result = await hass.services.async_call(
        DOMAIN,
        "process_workout",
        {
            "config_entry_id": entry.entry_id,
            "activity_id": "123",
            "calendar_entity": "calendar.training",
            "dry_run": False,
        },
        blocking=True,
        return_response=True,
    )
    assert result["status"] == "queued"
    assert not writes
    assert (
        entry.runtime_data.worker.queue.jobs[result["job_id"]]["last_error"]
        == "calendar_unavailable"
    )
    available = True
    freezer.tick(61)
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.runtime_data.worker.queue.jobs[result["job_id"]]["status"] == "done"
    assert activity["workout_type"] == 3
    assert writes == [{"workout_type": "3"}]

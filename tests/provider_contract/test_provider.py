"""Black-box contracts across both integrations, not manually fired HA events."""

import pytest
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.strava_activity_tools.const import DOMAIN, READY_EVENT

from .conftest import delivery


async def test_real_webhook_oauth_handoff_and_duplicate_delivery(hass, linked):
    client, _, requests, _ = linked
    events = async_capture_events(hass, READY_EVENT)
    challenge = await client.get("/api/strava/webhook?hub.challenge=synthetic-challenge")
    assert challenge.status == 200
    assert await challenge.json() == {"hub.challenge": "synthetic-challenge"}
    for _ in range(2):
        response = await client.post("/api/strava/webhook", json=delivery())
        assert response.status == 200
        await hass.async_block_till_done(wait_background_tasks=True)
    assert len(events) == 1
    assert events[0].data["config_entry_id"] == "tools42"
    assert events[0].data["activity_id"] == "9"
    assert requests == [("GET", "Bearer synthetic-42")]
    assert hass.data[DOMAIN]["tools99"].worker.status()["jobs"] == []


@pytest.mark.parametrize(
    "changes", [{"owner_id": 100}, {"subscription_id": 100}, {"aspect_type": "update"}]
)
async def test_other_owner_subscription_and_update_cannot_start_jobs(hass, linked, changes):
    client, _, requests, _ = linked
    events = async_capture_events(hass, READY_EVENT)
    assert (await client.post("/api/strava/webhook", json=delivery(**changes))).status == 200
    await hass.async_block_till_done(wait_background_tasks=True)
    assert events == []
    assert requests == []


async def test_preview_and_guarded_write_reuse_provider_oauth(hass, linked):
    _, activity, requests, _ = linked

    async def update(**extra):
        return await hass.services.async_call(
            DOMAIN,
            "update_activity",
            {
                "config_entry_id": "tools42",
                "activity_id": "9",
                "changes": {"hide_from_home": True},
                **extra,
            },
            blocking=True,
            return_response=True,
        )

    assert (await update())["status"] == "would_update"
    assert activity["hide_from_home"] is False
    assert (await update(dry_run=False))["status"] == "updated"
    assert activity["hide_from_home"] is True
    assert [method for method, _ in requests] == ["GET", "GET", "GET", "PUT", "GET"]
    assert {auth for _, auth in requests} == {"Bearer synthetic-42"}


async def test_source_unload_fails_closed_then_reload_rebinds_session(hass, linked):
    _, _, requests, entries = linked
    old_session = hass.data["ha_strava"]["source42"].oauth_session
    assert await hass.config_entries.async_unload("source42")
    with pytest.raises(HomeAssistantError, match="source_not_loaded"):
        await hass.services.async_call(
            DOMAIN,
            "get_activity",
            {"config_entry_id": "tools42", "activity_id": "9"},
            blocking=True,
            return_response=True,
        )
    assert requests == []
    source = entries[0]
    hass.config_entries.async_update_entry(
        source,
        data={**source.data, "token": {**source.data["token"], "access_token": "reloaded-42"}},
    )
    assert await hass.config_entries.async_setup("source42")
    assert hass.data["ha_strava"]["source42"].oauth_session is not old_session
    await hass.services.async_call(
        DOMAIN,
        "get_activity",
        {"config_entry_id": "tools42", "activity_id": "9"},
        blocking=True,
        return_response=True,
    )
    assert requests == [("GET", "Bearer reloaded-42")]


async def test_forged_known_owner_is_rejected_by_the_api_ownership_check(hass, linked):
    client, _, requests, _ = linked
    events = async_capture_events(hass, READY_EVENT)
    assert (await client.post("/api/strava/webhook", json=delivery(owner_id=99))).status == 200
    await hass.async_block_till_done(wait_background_tasks=True)
    assert events == []
    assert requests == [("GET", "Bearer synthetic-99")]
    jobs = hass.data[DOMAIN]["tools99"].worker.status()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["status"] == "failed"
    assert jobs[0]["last_error"] == "wrong_athlete"

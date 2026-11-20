"""Independent config-entry/actions path, with no source-provider runtime."""

import pytest
from homeassistant.exceptions import HomeAssistantError

from .conftest import DOMAIN
from .test_auth import account


@pytest.fixture
async def standalone(hass, oauth_app, oauth_responses, aioclient_mock):
    entry = account(hass, oauth_app)
    hass.config_entries.async_update_entry(
        entry, options={"preview_only": True, "webhook_enabled": False}
    )
    oauth_responses()
    aioclient_mock.get(
        "https://www.strava.com/api/v3/activities/123",
        json={
            "id": 123,
            "athlete": {"id": 42},
            "sport_type": "Run",
            "distance": 1609.344,
            "name": "Synthetic run",
            "description": "Private original notes",
            "workout_type": 3,
        },
    )
    aioclient_mock.get(
        "https://www.strava.com/api/v3/activities/999",
        json={
            "id": 999,
            "athlete": {"id": 99},
            "name": "Other athlete private activity",
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    return entry, aioclient_mock


async def call(hass, entry, name, **data):
    return await hass.services.async_call(
        DOMAIN,
        name,
        {"config_entry_id": entry.entry_id, **data},
        blocking=True,
        return_response=True,
    )


async def test_independent_services_owner_checked_and_preview_by_default(hass, standalone):
    entry, http = standalone
    result = await call(hass, entry, "get_activity", activity_id="123")
    assert result["athlete_id"] == "42"
    assert result["activity_id"] == "123"
    assert "description" not in result
    preview = await call(
        hass,
        entry,
        "update_activity",
        activity_id="123",
        changes={"hide_from_home": True},
        dry_run=False,
    )
    assert preview["status"] == "would_update"
    assert not any(c[0] == "PUT" for c in http.mock_calls)
    assert "ha_strava" not in hass.data


async def test_foreign_activity_is_neither_disclosed_nor_written(hass, standalone):
    entry, http = standalone
    with pytest.raises(HomeAssistantError, match="wrong_athlete"):
        await call(
            hass,
            entry,
            "update_activity",
            activity_id="999",
            changes={"hide_from_home": True},
            dry_run=False,
        )
    assert not any(c[0] == "PUT" for c in http.mock_calls)
    assert not entry.runtime_data.journal.recent(True)


async def test_jobs_history_and_services_survive_reload(hass, standalone):
    entry, _ = standalone
    await call(hass, entry, "update_activity", activity_id="123", changes={"commute": True})
    assert entry.runtime_data.journal.recent(True)
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert entry.runtime_data.journal.recent(True)[0]["activity_id"] == "123"
    result = await call(hass, entry, "list_jobs")
    assert result["stopped"] is False


async def test_short_workout_mute_exemption_remains(hass, standalone):
    entry, http = standalone
    result = await call(
        hass,
        entry,
        "mute_activity",
        activity_id="123",
        filters={"distance_below_miles": 2.1, "exclude_workouts": True},
    )
    assert result["status"] == "skipped_ineligible"
    assert not any(c[0] == "PUT" for c in http.mock_calls)

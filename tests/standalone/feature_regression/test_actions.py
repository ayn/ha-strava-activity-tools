"""Our retained action regressions exercised against independent OAuth setup."""

from unittest.mock import AsyncMock, patch

import pytest
from custom_components.ha_strava_automation.client import ActivityError
from custom_components.ha_strava_automation.const import DOMAIN
from homeassistant.exceptions import HomeAssistantError

from ..test_auth import account


@pytest.fixture
async def configured(hass, oauth_app, oauth_responses):
    entry = account(hass, oauth_app, entry_id="tools")
    hass.config_entries.async_update_entry(
        entry, options={"preview_only": False, "webhook_enabled": False}
    )
    oauth_responses()
    assert await hass.config_entries.async_setup(entry.entry_id)
    activity = {
        "id": 9,
        "athlete": {"id": 42},
        "name": "Morning Run",
        "device_name": "Watch",
        "sport_type": "Run",
        "distance": 1609.344,
        "description": "notes",
        "start_date": "2026-01-09T06:00:00Z",
        "start_latlng": [0, 0],
    }
    return entry, activity


async def test_service_defaults_preview_and_returns_response(hass, configured):
    entry, _ = configured
    client = hass.data[DOMAIN][entry.entry_id].client
    with patch.object(
        client, "update", AsyncMock(return_value={"status": "would_update"})
    ) as update:
        result = await hass.services.async_call(
            DOMAIN,
            "update_activity",
            {"config_entry_id": "tools", "activity_id": "9", "changes": {"hide_from_home": True}},
            blocking=True,
            return_response=True,
        )
    assert result["status"] == "would_update"
    update.assert_awaited_once_with("9", {"hide_from_home": True}, True)


async def test_matching_rejects_unknown_filters_with_friendly_error(hass, configured):
    entry, activity = configured
    with patch.object(
        hass.data[DOMAIN][entry.entry_id].client, "get", AsyncMock(return_value=activity)
    ):
        with pytest.raises(HomeAssistantError, match="invalid_filters"):
            await hass.services.async_call(
                DOMAIN,
                "match_activity",
                {"config_entry_id": "tools", "activity_id": "9", "filters": {"typo": "value"}},
                blocking=True,
                return_response=True,
            )


async def test_environment_uses_historical_recorder_and_explicit_write(hass, configured):
    entry, activity = configured
    client = hass.data[DOMAIN][entry.entry_id].client
    hass.states.async_set("zone.home", "0", {"latitude": 0, "longitude": 0})
    with (
        patch.object(client, "get", AsyncMock(return_value=activity)),
        patch(
            "custom_components.ha_strava_automation.actions.enrich_description",
            AsyncMock(return_value={"status": "updated"}),
        ) as enrich,
        patch(
            "custom_components.ha_strava_automation.actions.dt_util.utcnow",
            return_value=__import__("datetime").datetime(
                2026, 1, 10, tzinfo=__import__("datetime").UTC
            ),
        ),
        patch(
            "custom_components.ha_strava_automation.actions.async_readings",
            AsyncMock(return_value=[35, 0]),
        ) as history,
    ):
        result = await hass.services.async_call(
            DOMAIN,
            "append_environment",
            {
                "config_entry_id": "tools",
                "activity_id": "9",
                "aqi_entity": "sensor.aqi",
                "uv_entity": "sensor.uv",
                "dry_run": False,
            },
            blocking=True,
            return_response=True,
        )
        assert result["status"] == "done"
        history.assert_awaited_once_with(hass, activity["start_date"], ["sensor.aqi", "sensor.uv"])
        enrich.assert_awaited_once_with(client, "9", 35, 0, False)


@pytest.mark.parametrize(
    "mode",
    [
        "away",
        "missing_history",
        "local",
        "annotated",
        "indoor",
        "disabled",
        "no_home",
        "recorder_missing",
        "api_missing",
    ],
)
async def test_external_weather_fallback_policy(hass, configured, mode):
    from datetime import UTC, datetime

    from custom_components.ha_strava_automation.actions import environment_task
    from custom_components.ha_strava_automation.weather import CREDIT

    entry, activity = configured
    activity = {**activity}
    if mode in ("away", "disabled", "api_missing"):
        activity["start_latlng"] = [10, 20]
    if mode == "indoor":
        activity["trainer"] = True
    if mode == "annotated":
        activity["description"] = "notes\nAQI 35 💚\nUV Index 0 🟢"
    client = hass.data[DOMAIN][entry.entry_id].client
    hass.states.async_set("zone.home", "0", {"latitude": 0, "longitude": 0})
    if mode == "no_home":
        hass.states.async_remove("zone.home")
    params = {
        "aqi_entity": "sensor.aqi",
        "uv_entity": "sensor.uv",
        "radius_km": 15,
        "dry_run": True,
        "external_fallback": mode != "disabled",
    }
    with (
        patch.object(client, "get", AsyncMock(return_value=activity)),
        patch(
            "custom_components.ha_strava_automation.actions.dt_util.utcnow",
            return_value=datetime(2026, 1, 10, tzinfo=UTC),
        ),
        patch(
            "custom_components.ha_strava_automation.actions.async_readings",
            AsyncMock(
                return_value=[35, None] if mode == "missing_history" else [35, 0],
                side_effect=ActivityError("recorder_unavailable")
                if mode == "recorder_missing"
                else None,
            ),
        ) as history,
        patch(
            "custom_components.ha_strava_automation.actions.async_weather_readings",
            AsyncMock(return_value=[None, None] if mode == "api_missing" else [70, 2]),
        ) as weather,
        patch(
            "custom_components.ha_strava_automation.actions.enrich_description",
            AsyncMock(return_value={"status": "would_update"}),
        ) as enrich,
    ):
        if mode == "api_missing":
            with pytest.raises(ActivityError, match="weather_unavailable"):
                await environment_task(hass, client, "9", params)
            enrich.assert_not_awaited()
            return
        result = await environment_task(hass, client, "9", params)
        if mode in ("away", "missing_history", "no_home", "recorder_missing"):
            weather.assert_awaited_once_with(hass, activity["start_date"], activity["start_latlng"])
            enrich.assert_awaited_once_with(
                client, "9", 35 if mode == "missing_history" else 70, 2, True, source=CREDIT
            )
            if mode == "away":
                history.assert_not_awaited()
        else:
            weather.assert_not_awaited()
        if mode in ("indoor", "disabled"):
            assert result["status"] == "skipped_ineligible"
        if mode in ("local", "annotated"):
            assert [result["aqi"], result["uv"]] == [35, 0]


async def test_weather_fallback_flag_persists_in_durable_job(hass, configured):
    entry, activity = configured
    runtime = hass.data[DOMAIN][entry.entry_id]
    with patch.object(
        runtime.client, "get", AsyncMock(side_effect=ActivityError("source_not_loaded"))
    ):
        result = await hass.services.async_call(
            DOMAIN,
            "append_environment",
            {
                "config_entry_id": "tools",
                "activity_id": "9",
                "aqi_entity": "sensor.aqi",
                "uv_entity": "sensor.uv",
                "external_fallback": True,
                "dry_run": False,
            },
            blocking=True,
            return_response=True,
        )
    assert result["status"] == "queued"
    saved = await runtime.worker.queue.store.async_load()
    job = saved["jobs"][result["job_id"]]
    assert job["parameters"]["external_fallback"] is True


@pytest.mark.parametrize("explicit", [True, False, "with_ha_sensors"])
async def test_location_api_needs_no_home_zone_or_sensors(hass, configured, explicit):
    from datetime import UTC, datetime

    from custom_components.ha_strava_automation.weather import CREDIT

    entry, activity = configured
    hass.states.async_remove("zone.home")
    client = hass.data[DOMAIN][entry.entry_id].client
    data = {"config_entry_id": "tools", "activity_id": "9", "dry_run": True}
    if explicit:
        data["weather_source"] = "location_api"
    if explicit == "with_ha_sensors":
        hass.states.async_set("zone.home", "0", {"latitude": 0, "longitude": 0})
        data.update(aqi_entity="sensor.aqi", uv_entity="sensor.uv")
    with (
        patch.object(client, "get", AsyncMock(return_value=activity)),
        patch(
            "custom_components.ha_strava_automation.actions.dt_util.utcnow",
            return_value=datetime(2026, 1, 10, tzinfo=UTC),
        ),
        patch(
            "custom_components.ha_strava_automation.actions.async_readings", AsyncMock()
        ) as history,
        patch(
            "custom_components.ha_strava_automation.actions.async_weather_readings",
            AsyncMock(return_value=[70, 2]),
        ) as weather,
        patch(
            "custom_components.ha_strava_automation.actions.enrich_description",
            AsyncMock(return_value={"status": "would_update"}),
        ) as enrich,
    ):
        result = await hass.services.async_call(
            DOMAIN, "append_environment", data, blocking=True, return_response=True
        )
    assert result["status"] == "would_update"
    history.assert_not_awaited()
    weather.assert_awaited_once_with(hass, activity["start_date"], activity["start_latlng"])
    enrich.assert_awaited_once_with(client, "9", 70, 2, True, source=CREDIT)


async def test_location_api_durable_job_needs_no_ha_sensor_references(hass, configured):
    entry, _ = configured
    runtime = hass.data[DOMAIN][entry.entry_id]
    with patch.object(
        runtime.client, "get", AsyncMock(side_effect=ActivityError("source_not_loaded"))
    ):
        result = await hass.services.async_call(
            DOMAIN,
            "append_environment",
            {
                "config_entry_id": "tools",
                "activity_id": "9",
                "weather_source": "location_api",
                "dry_run": False,
            },
            blocking=True,
            return_response=True,
        )
    assert result["status"] == "queued"
    params = runtime.worker.queue.jobs[result["job_id"]]["parameters"]
    assert params == {"weather_source": "location_api", "dry_run": False}


async def test_calendar_suggestion_service_is_read_only(hass, configured):
    entry, activity = configured
    activity.update(
        timezone="UTC",
        workout_type=None,
        laps=[{"distance": 400, "moving_time": 80 if i % 2 == 0 else 140} for i in range(8)],
    )
    calls = []

    async def calendar_response(call):
        calls.append(dict(call.data))
        return {
            "calendar.training": {
                "events": [{"start": "2026-01-09", "end": "2026-01-10", "summary": "4 x 400m"}]
            }
        }

    from homeassistant.core import SupportsResponse

    hass.services.async_register(
        "calendar", "get_events", calendar_response, supports_response=SupportsResponse.ONLY
    )
    client = hass.data[DOMAIN][entry.entry_id].client
    with (
        patch.object(client, "get", AsyncMock(return_value=activity)),
        patch.object(client, "update", AsyncMock()) as update,
    ):
        result = await hass.services.async_call(
            DOMAIN,
            "suggest_workout",
            {
                "config_entry_id": entry.entry_id,
                "activity_id": "9",
                "calendar_entity": "calendar.training",
            },
            blocking=True,
            return_response=True,
        )
    assert result["changes"] == {"workout_type": 3}
    assert result["preview_only"] is True
    assert result["is_workout"] is True
    assert result["expected"] == {
        key: activity.get(key)
        for key in ("sport_type", "workout_type", "gear_id", "trainer", "manual")
    }
    assert result["on_track"] is False
    assert not update.called
    assert calls[0]["start_date_time"] == "2026-01-09T00:00:00+00:00"
    assert calls[0]["end_date_time"] == "2026-01-10T00:00:00+00:00"


async def test_calendar_suggestion_fails_closed_when_calendar_unavailable(hass, configured):
    entry, activity = configured
    with patch.object(
        hass.data[DOMAIN][entry.entry_id].client, "get", AsyncMock(return_value=activity)
    ):
        with pytest.raises(HomeAssistantError, match="calendar_unavailable"):
            await hass.services.async_call(
                DOMAIN,
                "suggest_workout",
                {
                    "config_entry_id": entry.entry_id,
                    "activity_id": "9",
                    "calendar_entity": "calendar.training",
                },
                blocking=True,
                return_response=True,
            )


async def test_detect_track_action_needs_no_calendar_or_shoe_config(hass, configured):
    entry, activity = configured
    with (
        patch.object(
            hass.data[DOMAIN][entry.entry_id].client, "get", AsyncMock(return_value=activity)
        ),
        patch(
            "custom_components.ha_strava_automation.actions.track_evidence",
            return_value={"on_track": True, "track_fraction": 1},
        ),
    ):
        result = await hass.services.async_call(
            DOMAIN,
            "detect_track",
            {"config_entry_id": entry.entry_id, "activity_id": "9"},
            blocking=True,
            return_response=True,
        )
    assert result == {
        "activity_id": "9",
        "likely_track": True,
        "on_track": True,
        "track_fraction": 1,
        "method": "gps_geometry",
        "preview_only": True,
    }

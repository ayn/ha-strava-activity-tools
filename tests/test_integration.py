"""Real HA services/config flow/event bus with only OAuth/recorder mocked."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import data_entry_flow
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.strava_activity_tools import async_setup, async_setup_entry
from custom_components.strava_activity_tools.client import ActivityError
from custom_components.strava_activity_tools.const import DOMAIN, READY_EVENT, SOURCE_EVENT


@pytest.fixture
async def configured(hass):
    source = MockConfigEntry(
        domain="ha_strava", unique_id="42", title="Athlete", entry_id="source", data={}
    )
    source.add_to_hass(hass)
    session = SimpleNamespace(async_request=AsyncMock())
    hass.data["ha_strava"] = {source.entry_id: SimpleNamespace(oauth_session=session)}
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="42",
        entry_id="tools",
        data={"source_entry_id": "source", "athlete_id": "42"},
        options={"preview_only": False},
    )
    entry.add_to_hass(hass)
    await async_setup(hass, {})
    hass.config.components.add(DOMAIN)
    async with entry.setup_lock:
        await async_setup_entry(hass, entry)
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


async def test_flow_no_source_aborts(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "source_not_loaded"


async def test_flow_rejects_wrong_owner_then_creates(hass):
    source = MockConfigEntry(domain="ha_strava", unique_id="42", title="Athlete", data={})
    source.add_to_hass(hass)
    hass.data["ha_strava"] = {source.entry_id: SimpleNamespace(oauth_session=AsyncMock())}
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    with patch("custom_components.strava_activity_tools.async_setup_entry", return_value=True):
        invalid = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"source_entry_id": source.entry_id, "athlete_id": "99"}
        )
        assert invalid["errors"] == {"athlete_id": "wrong_athlete"}
        created = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"source_entry_id": source.entry_id, "athlete_id": "42"}
        )
        assert created["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
        assert set(created["data"]) == {"source_entry_id", "athlete_id"}


async def test_webhook_enriches_correct_owner_and_deduplicates(hass, configured):
    entry, activity = configured
    client = hass.data[DOMAIN][entry.entry_id].client
    events = async_capture_events(hass, READY_EVENT)
    with patch.object(client, "get", AsyncMock(return_value=activity)) as get:
        hass.bus.async_fire(SOURCE_EVENT, {"athlete_id": "99", "activity_id": "9"})
        await hass.async_block_till_done(wait_background_tasks=True)
        assert not get.called
        hass.bus.async_fire(SOURCE_EVENT, {"athlete_id": "42", "activity_id": "9"})
        hass.bus.async_fire(SOURCE_EVENT, {"athlete_id": "42", "activity_id": "9"})
        await hass.async_block_till_done(wait_background_tasks=True)
        assert get.call_count == 1
        assert len(events) == 1
        assert events[0].data["distance_miles"] == 1
        assert events[0].data["config_entry_id"] == "tools"
        assert "start_latlng" not in events[0].data
        assert "description" not in events[0].data


async def test_service_defaults_preview_and_returns_response(hass, configured):
    entry, _ = configured
    client = hass.data[DOMAIN][entry.entry_id].client
    with patch.object(
        client, "update", AsyncMock(return_value={"status": "would_update"})
    ) as update:
        result = await hass.services.async_call(
            DOMAIN,
            "update_activity",
            {
                "config_entry_id": "tools",
                "activity_id": "9",
                "changes": {"hide_from_home": True},
            },
            blocking=True,
            return_response=True,
        )
    assert result["status"] == "would_update"
    update.assert_awaited_once_with("9", {"hide_from_home": True}, True)


async def test_services_fail_when_source_unloaded(hass, configured):
    hass.data["ha_strava"].clear()
    with pytest.raises(HomeAssistantError, match="source_not_loaded"):
        await hass.services.async_call(
            DOMAIN,
            "get_activity",
            {
                "config_entry_id": "tools",
                "activity_id": "9",
            },
            blocking=True,
            return_response=True,
        )


async def test_matching_rejects_unknown_filters_with_friendly_error(hass, configured):
    entry, activity = configured
    with patch.object(
        hass.data[DOMAIN][entry.entry_id].client, "get", AsyncMock(return_value=activity)
    ):
        with pytest.raises(HomeAssistantError, match="invalid_filters"):
            await hass.services.async_call(
                DOMAIN,
                "match_activity",
                {
                    "config_entry_id": "tools",
                    "activity_id": "9",
                    "filters": {"typo": "value"},
                },
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
            "custom_components.strava_activity_tools.enrich_description",
            AsyncMock(return_value={"status": "updated"}),
        ) as enrich,
        patch(
            "custom_components.strava_activity_tools.dt_util.utcnow",
            return_value=__import__("datetime").datetime(
                2026, 1, 10, tzinfo=__import__("datetime").UTC
            ),
        ),
        patch(
            "custom_components.strava_activity_tools.async_readings",
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

    from custom_components.strava_activity_tools import environment_task
    from custom_components.strava_activity_tools.weather import CREDIT

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
            "custom_components.strava_activity_tools.dt_util.utcnow",
            return_value=datetime(2026, 1, 10, tzinfo=UTC),
        ),
        patch(
            "custom_components.strava_activity_tools.async_readings",
            AsyncMock(
                return_value=[35, None] if mode == "missing_history" else [35, 0],
                side_effect=ActivityError("recorder_unavailable")
                if mode == "recorder_missing"
                else None,
            ),
        ) as history,
        patch(
            "custom_components.strava_activity_tools.async_weather_readings",
            AsyncMock(return_value=[None, None] if mode == "api_missing" else [70, 2]),
        ) as weather,
        patch(
            "custom_components.strava_activity_tools.enrich_description",
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

    from custom_components.strava_activity_tools.weather import CREDIT

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
            "custom_components.strava_activity_tools.dt_util.utcnow",
            return_value=datetime(2026, 1, 10, tzinfo=UTC),
        ),
        patch("custom_components.strava_activity_tools.async_readings", AsyncMock()) as history,
        patch(
            "custom_components.strava_activity_tools.async_weather_readings",
            AsyncMock(return_value=[70, 2]),
        ) as weather,
        patch(
            "custom_components.strava_activity_tools.enrich_description",
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


async def test_nonretryable_error_emits_category_only(hass, configured):
    entry, _ = configured
    from custom_components.strava_activity_tools.const import ERROR_EVENT

    events = async_capture_events(hass, ERROR_EVENT)
    with patch.object(
        hass.data[DOMAIN][entry.entry_id].client,
        "get",
        AsyncMock(side_effect=ActivityError("reauth_required")),
    ):
        hass.bus.async_fire(SOURCE_EVENT, {"athlete_id": "42", "activity_id": "9"})
        await hass.async_block_till_done(wait_background_tasks=True)
    assert len(events) == 1
    assert events[0].data["category"] == "reauth_required"


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Allow the local integration to load in the HA test harness."""


async def test_transient_fetch_retries_then_emits_once(hass, configured, freezer):
    entry, activity = configured
    from custom_components.strava_activity_tools.const import ERROR_EVENT

    ready = async_capture_events(hass, READY_EVENT)
    errors = async_capture_events(hass, ERROR_EVENT)
    runtime = hass.data[DOMAIN][entry.entry_id]
    get = AsyncMock(side_effect=[ActivityError("upstream_unavailable"), activity])
    with patch.object(runtime.client, "get", get):
        hass.bus.async_fire(SOURCE_EVENT, {"athlete_id": "42", "activity_id": "9"})
        await hass.async_block_till_done(wait_background_tasks=True)
        assert get.await_count == 1
        assert not ready
        freezer.tick(61)
        await runtime.worker.process()
        await hass.async_block_till_done(wait_background_tasks=True)
    assert get.call_count == 2
    assert len(ready) == 1
    assert not errors


async def test_source_unloaded_during_retry_is_preserved(hass, configured, freezer):
    entry, _ = configured
    runtime = hass.data[DOMAIN][entry.entry_id]

    async def first_fetch(_):
        hass.data["ha_strava"].clear()
        raise ActivityError("upstream_unavailable")

    with patch.object(runtime.client, "get", first_fetch):
        hass.bus.async_fire(SOURCE_EVENT, {"athlete_id": "42", "activity_id": "9"})
        await hass.async_block_till_done(wait_background_tasks=True)
        freezer.tick(61)
        await runtime.worker.process()
    assert len(runtime.worker.queue.jobs) == 1
    job = next(iter(runtime.worker.queue.jobs.values()))
    assert job["status"] == "queued"
    assert job["last_error"] == "source_not_loaded"


async def test_options_flow_preview_lock_defaults_on(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="42",
        entry_id="tools",
        data={"source_entry_id": "source", "athlete_id": "42"},
    )
    entry.add_to_hass(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["type"] is data_entry_flow.FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"preview_only": True}
    )
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options["preview_only"] is True


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


async def test_track_shoes_options_use_own_catalog_and_can_be_changed_or_disabled(hass, configured):
    entry, _ = configured
    catalog = [
        {"id": "g123", "name": "Track shoe", "primary": False},
        {"id": "g456", "name": "Replacement shoe", "primary": True},
    ]
    with patch(
        "custom_components.strava_activity_tools.config_flow.ActivityClient.shoes",
        AsyncMock(return_value=catalog),
    ):
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        field = next(k for k in flow["data_schema"].schema if str(k) == "track_shoes")
        choices = flow["data_schema"].schema[field].config["options"]
        assert {x["value"] for x in choices} == {"disabled", "g123", "g456"}
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"preview_only": False, "track_shoes": "g123"}
        )
        assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
        assert entry.options["track_shoes"] == "g123"
        for new in ("g456", "disabled"):
            flow = await hass.config_entries.options.async_init(entry.entry_id)
            await hass.config_entries.options.async_configure(
                flow["flow_id"], {"preview_only": False, "track_shoes": new}
            )
            assert entry.options["track_shoes"] == new


async def test_track_shoes_options_do_not_accept_other_accounts_gear(hass, configured):
    entry, _ = configured
    with patch(
        "custom_components.strava_activity_tools.config_flow.ActivityClient.shoes",
        AsyncMock(return_value=[{"id": "g123", "name": "Owned shoe", "primary": False}]),
    ):
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        with pytest.raises(data_entry_flow.InvalidData):
            await hass.config_entries.options.async_configure(
                flow["flow_id"], {"preview_only": False, "track_shoes": "g999"}
            )
    assert "track_shoes" not in entry.options


async def test_detect_track_action_needs_no_calendar_or_shoe_config(hass, configured):
    entry, activity = configured
    with (
        patch.object(
            hass.data[DOMAIN][entry.entry_id].client, "get", AsyncMock(return_value=activity)
        ),
        patch(
            "custom_components.strava_activity_tools.track_evidence",
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


async def test_missing_shoe_scope_does_not_block_preview_lock_and_can_start_source_reauth(
    hass, configured
):
    entry, _ = configured
    source = hass.config_entries.async_get_entry("source")
    with (
        patch(
            "custom_components.strava_activity_tools.config_flow.ActivityClient.shoes",
            AsyncMock(side_effect=ActivityError("shoe_catalog_unavailable")),
        ),
        patch.object(type(source), "async_start_reauth") as reauth,
    ):
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        assert not flow.get("errors")
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"preview_only": True, "reauthorize_strava": True}
        )
        assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
        assert entry.options["preview_only"] is True
        assert "reauthorize_strava" not in entry.options
        reauth.assert_called_once_with(hass)


async def test_service_passes_preview_preconditions_and_retains_write_lock(hass, configured):
    entry, _ = configured
    client = hass.data[DOMAIN][entry.entry_id].client
    expected = {"sport_type": "Run", "gear_id": None, "workout_type": None}
    for locked in (False, True):
        hass.config_entries.async_update_entry(entry, options={"preview_only": locked})
        with patch.object(
            client, "update", AsyncMock(return_value={"status": "would_update"})
        ) as update:
            await hass.services.async_call(
                DOMAIN,
                "update_activity",
                {
                    "config_entry_id": entry.entry_id,
                    "activity_id": "9",
                    "changes": {"workout_type": 3},
                    "expected": expected,
                    "dry_run": False,
                },
                blocking=True,
                return_response=True,
            )
            update.assert_awaited_once_with("9", {"workout_type": 3}, locked, expected=expected)

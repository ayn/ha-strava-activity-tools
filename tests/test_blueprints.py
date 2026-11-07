"""Run packaged blueprints through HA's real automation engine."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.strava_activity_tools import async_setup, async_setup_entry
from custom_components.strava_activity_tools.const import DOMAIN, READY_EVENT


async def setup_blueprint(hass, name, inputs):
    source = Path(__file__).parents[1] / "blueprints/automation" / f"{name}.yaml"
    target = Path(hass.config.path("blueprints/automation/strava_activity_tools"))
    target.mkdir(parents=True, exist_ok=True)
    (target / source.name).write_text(source.read_text())
    await async_setup(hass, {})
    hass.data["ha_strava"] = {"source": SimpleNamespace(oauth_session=AsyncMock())}
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="42",
        entry_id="tools",
        data={"source_entry_id": "source", "athlete_id": "42"},
        options={"preview_only": False},
    )
    entry.add_to_hass(hass)
    await async_setup_entry(hass, entry)
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": [
                {
                    "alias": "Test activity blueprint",
                    "id": "synthetic_activity_rule",
                    "use_blueprint": {
                        "path": f"strava_activity_tools/{source.name}",
                        "input": inputs,
                    },
                }
            ]
        },
    )
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids("automation")) == 1
    return hass.data[DOMAIN]["tools"].client


@pytest.mark.parametrize(
    ("distance", "should_write"), [(3200, True), (3379.6224, False), (5000, False)]
)
async def test_mute_short_run_actual_automation(hass, distance, should_write):
    client = await setup_blueprint(
        hass,
        "mute_activity",
        {
            "config_entry_id": "tools",
            "sport_types": ["Run"],
            "distance_below_miles": 2.1,
        },
    )
    activity = {"id": 9, "athlete": {"id": 42}, "sport_type": "Run", "distance": distance}
    with (
        patch.object(client, "get", AsyncMock(return_value=activity)),
        patch.object(
            client, "update", AsyncMock(return_value={"status": "would_update"})
        ) as update,
    ):
        hass.bus.async_fire(READY_EVENT, {"config_entry_id": "tools", "activity_id": "9"})
        await hass.async_block_till_done()
        assert update.called is should_write
        if should_write:
            update.assert_awaited_once_with("9", {"hide_from_home": True}, True)


async def test_mute_different_account_does_not_run(hass):
    client = await setup_blueprint(hass, "mute_activity", {"config_entry_id": "tools"})
    with patch.object(client, "get", AsyncMock()) as get:
        hass.bus.async_fire(READY_EVENT, {"config_entry_id": "someone_else", "activity_id": "9"})
        await hass.async_block_till_done()
        assert not get.called


async def test_gear_blueprint_explicit_write(hass):
    client = await setup_blueprint(
        hass,
        "assign_gear",
        {
            "config_entry_id": "tools",
            "sport_types": ["Ride"],
            "gear_id": "b123",
            "dry_run": False,
        },
    )
    with (
        patch.object(
            client,
            "get",
            AsyncMock(return_value={"id": 9, "athlete": {"id": 42}, "sport_type": "Ride"}),
        ),
        patch.object(client, "update", AsyncMock(return_value={"status": "updated"})) as update,
    ):
        hass.bus.async_fire(READY_EVENT, {"config_entry_id": "tools", "activity_id": "9"})
        await hass.async_block_till_done()
        update.assert_awaited_once_with("9", {"gear_id": "b123"}, False)


async def test_environment_blueprint_selects_sensors(hass):
    await setup_blueprint(
        hass,
        "append_environment",
        {
            "config_entry_id": "tools",
            "aqi_entity": "sensor.us_aqi",
            "uv_entity": "sensor.uv",
        },
    )
    with patch("custom_components.strava_activity_tools.async_readings", AsyncMock()) as history:
        # No current-state fallback when historical weather is unavailable.
        hass.bus.async_fire(READY_EVENT, {"config_entry_id": "someone_else", "activity_id": "9"})
        await hass.async_block_till_done()
        assert not history.called


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Allow the local integration to load in the HA test harness."""


async def test_blank_mute_filters_cannot_accidentally_mute_everything(hass):
    client = await setup_blueprint(
        hass, "mute_activity", {"config_entry_id": "tools", "dry_run": False}
    )
    with (
        patch.object(client, "get", AsyncMock(return_value={"id": 9, "athlete": {"id": 42}})),
        patch.object(client, "update", AsyncMock()) as update,
    ):
        hass.bus.async_fire(READY_EVENT, {"config_entry_id": "tools", "activity_id": "9"})
        await hass.async_block_till_done()
        assert not update.called


async def test_environment_blueprint_runs_historical_enrichment(hass):
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    client = await setup_blueprint(
        hass,
        "append_environment",
        {
            "config_entry_id": "tools",
            "aqi_entity": "sensor.us_aqi",
            "uv_entity": "sensor.uv",
        },
    )
    hass.states.async_set("zone.home", "0", {"latitude": 0, "longitude": 0})
    activity = {
        "id": 9,
        "athlete": {"id": 42},
        "sport_type": "Run",
        "description": "notes",
        "start_date": (dt_util.utcnow() - timedelta(hours=2)).isoformat(),
        "start_latlng": [0, 0],
    }
    with (
        patch.object(client, "get", AsyncMock(return_value=activity)),
        patch.object(client, "transform", wraps=client.transform) as transform,
        patch(
            "custom_components.strava_activity_tools.async_readings",
            AsyncMock(return_value=[35, 0]),
        ) as history,
    ):
        hass.bus.async_fire(READY_EVENT, {"config_entry_id": "tools", "activity_id": "9"})
        await hass.async_block_till_done()
        assert transform.await_count == 1
        assert transform.call_args.args[0] == "9"
        assert transform.call_args.args[2] is True
        history.assert_awaited_once_with(
            hass, activity["start_date"], ["sensor.us_aqi", "sensor.uv"]
        )


async def test_environment_blueprint_defaults_to_api_without_sensors(hass):
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    client = await setup_blueprint(hass, "append_environment", {"config_entry_id": "tools"})
    hass.states.async_remove("zone.home")
    activity = {
        "id": 9,
        "athlete": {"id": 42},
        "sport_type": "Run",
        "start_date": (dt_util.utcnow() - timedelta(hours=2)).isoformat(),
        "start_latlng": [10, 20],
        "description": "notes",
    }
    with (
        patch.object(client, "get", AsyncMock(return_value=activity)),
        patch.object(client, "transform", wraps=client.transform) as transform,
        patch("custom_components.strava_activity_tools.async_readings", AsyncMock()) as history,
        patch(
            "custom_components.strava_activity_tools.async_weather_readings",
            AsyncMock(return_value=[42, 2]),
        ) as weather,
    ):
        hass.bus.async_fire(READY_EVENT, {"config_entry_id": "tools", "activity_id": "9"})
        await hass.async_block_till_done()
        history.assert_not_awaited()
        weather.assert_awaited_once()
        assert transform.call_args.args[2] is True

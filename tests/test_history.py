"""Exercise actual recorder SQL history, not just a fabricated readings response."""

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.strava_activity_tools.client import ActivityError
from custom_components.strava_activity_tools.history import async_readings


async def test_real_recorder_snapshot_at_activity_start(recorder_mock, hass):
    hass.states.async_set("sensor.us_aqi", "35")
    hass.states.async_set("sensor.uv", "0")
    await async_wait_recording_done(hass)
    start = dt_util.utcnow().isoformat()
    hass.states.async_set("sensor.us_aqi", "95")
    hass.states.async_set("sensor.uv", "8")
    await async_wait_recording_done(hass)
    assert await async_readings(hass, start, ["sensor.us_aqi", "sensor.uv"]) == [35, 0]


async def test_missing_recorder_is_explicit(hass):
    with pytest.raises(ActivityError, match="recorder_unavailable"):
        await async_readings(hass, dt_util.utcnow().isoformat(), ["sensor.aqi", "sensor.uv"])

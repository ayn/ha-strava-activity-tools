"""Independent account history entities retain separate, sanitized streams."""

from unittest.mock import AsyncMock, patch

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.ha_strava_automation import async_unload_entry
from custom_components.ha_strava_automation.const import DOMAIN

from . import test_features

standalone = test_features.standalone


def entities(hass, entry):
    registry = er.async_get(hass)
    return {
        kind: registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_latest_{kind}")
        for kind in ("preview", "run")
    }


async def test_native_history_streams_are_sanitized_and_survive_reload(hass, standalone):
    entry, _ = standalone
    ids = entities(hass, entry)
    assert hass.states.get(ids["run"]).state == "no_activity"
    journal = entry.runtime_data.journal
    journal.record(
        "update_activity",
        "123",
        True,
        {"status": "would_update", "changes": {"description": "Synthetic private notes"}},
    )
    journal.record("append_environment", "124", False, {"status": "updated", "aqi": 40, "uv": 0})
    await hass.async_block_till_done()
    preview = hass.states.get(ids["preview"])
    actual = hass.states.get(ids["run"])
    assert preview.state == "would_update"
    assert preview.attributes["changes"] == {"description": "changed"}
    assert actual.attributes["activity_id"] == "124"
    assert preview.attributes["activity_id"] == "123"
    assert "Synthetic private notes" not in str(preview.attributes)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(ids["run"]).attributes["activity_id"] == "124"
    assert hass.states.get(ids["preview"]).attributes["activity_id"] == "123"


async def test_failed_platform_unload_keeps_live_worker(hass, standalone):
    entry, _ = standalone
    runtime = entry.runtime_data
    with patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)):
        assert not await async_unload_entry(hass, entry)
    assert hass.data[DOMAIN][entry.entry_id] is runtime
    assert not runtime.worker.closed


async def test_history_expires_without_new_upload(hass, standalone, freezer):
    entry, _ = standalone
    ids = entities(hass, entry)
    entry.runtime_data.journal.record("update_activity", "123", True, {"status": "would_update"})
    freezer.tick(8 * 86400)
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get(ids["preview"]).state == "no_activity"
    assert hass.states.get(ids["preview"]).attributes["recent"] == []

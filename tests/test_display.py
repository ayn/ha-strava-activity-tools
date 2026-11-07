"""Exercise native entities and the real service-to-history path."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.strava_activity_tools.client import ActivityError
from custom_components.strava_activity_tools.const import DOMAIN, SOURCE_EVENT


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Load the component from this checkout."""


@pytest.fixture
async def display(hass):
    source = MockConfigEntry(domain="ha_strava", unique_id="42", entry_id="source")
    source.add_to_hass(hass)
    hass.data["ha_strava"] = {"source": SimpleNamespace(oauth_session=AsyncMock())}
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="tools",
        unique_id="42",
        title="Test account",
        data={"source_entry_id": "source", "athlete_id": "42"},
        options={"preview_only": False},
    )
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entities = {
        key: registry.async_get_entity_id("sensor", DOMAIN, f"tools_latest_{key}")
        for key in ("preview", "run")
    }
    assert all(entities.values())
    return entry, hass.data[DOMAIN][entry.entry_id], entities


async def call_action(hass, action="update_activity", **kwargs):
    return await hass.services.async_call(
        DOMAIN,
        action,
        {"config_entry_id": "tools", "activity_id": "9", **kwargs},
        blocking=True,
        return_response=True,
    )


async def test_native_device_preview_and_write_results_are_visible_and_survive_reload(
    hass, display
):
    entry, runtime, entities = display
    assert hass.states.get(entities["preview"]).state == "no_activity"
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert device and device.config_entry_id == entry.entry_id
    changes = {"hide_from_home": True}
    with patch.object(
        runtime.client,
        "update",
        AsyncMock(
            return_value={
                "status": "would_update",
                "changes": changes,
            }
        ),
    ) as update:
        await call_action(hass, changes=changes)
        update.assert_awaited_once_with("9", changes, True)
    preview = hass.states.get(entities["preview"])
    assert preview.state == "would_update"
    assert preview.attributes["changes"] == changes
    assert preview.attributes["count"] == 1
    assert preview.attributes["stream"] == "previews"
    assert hass.states.get(entities["run"]).attributes["recent"] == []
    with patch.object(
        runtime.client,
        "update",
        AsyncMock(
            return_value={
                "status": "updated",
                "changes": changes,
            }
        ),
    ):
        await call_action(hass, changes=changes, dry_run=False)
    assert hass.states.get(entities["run"]).state == "updated"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entities["preview"]).state == "would_update"
    assert hass.states.get(entities["run"]).state == "updated"
    assert hass.data[DOMAIN][entry.entry_id].journal is not runtime.journal


async def test_integration_lock_still_prevents_writes_and_records_preview(hass, display):
    entry, runtime, entities = display
    hass.config_entries.async_update_entry(entry, options={"preview_only": True})
    with patch.object(
        runtime.client, "update", AsyncMock(return_value={"status": "would_update"})
    ) as update:
        await call_action(hass, changes={"hide_from_home": True}, dry_run=False)
    update.assert_awaited_once_with("9", {"hide_from_home": True}, True)
    assert hass.states.get(entities["preview"]).state == "would_update"
    assert hass.states.get(entities["run"]).state == "no_activity"


async def test_failures_are_visible_without_foreign_account_data(hass, display):
    _, runtime, entities = display
    with patch.object(
        runtime.client, "update", AsyncMock(side_effect=ActivityError("reauth_required"))
    ):
        with pytest.raises(HomeAssistantError, match="reauth_required"):
            await call_action(hass, changes={"hide_from_home": True}, dry_run=False)
    result = hass.states.get(entities["run"])
    assert result.state == "failed"
    assert result.attributes["error"] == "reauth_required"
    with patch.object(
        runtime.client, "update", AsyncMock(side_effect=ActivityError("wrong_athlete"))
    ):
        with pytest.raises(HomeAssistantError, match="wrong_athlete"):
            await call_action(hass, changes={"hide_from_home": True}, dry_run=False)
    assert hass.states.get(entities["run"]).attributes["count"] == 1


async def test_background_retry_and_recovery_appear_without_a_service_trace(hass, display, freezer):
    _, runtime, entities = display
    activity = {"id": 9, "athlete": {"id": 42}, "distance": 1000, "sport_type": "Run"}
    with patch.object(
        runtime.client,
        "get",
        AsyncMock(side_effect=[ActivityError("upstream_unavailable"), activity]),
    ):
        hass.bus.async_fire(SOURCE_EVENT, {"athlete_id": "42", "activity_id": "9"})
        await hass.async_block_till_done(wait_background_tasks=True)
        result = hass.states.get(entities["run"])
        assert result.state == "queued"
        assert result.attributes["attempt"] == 1
        assert result.attributes["error"] == "upstream_unavailable"
        freezer.tick(61)
        await runtime.worker.process()
    result = hass.states.get(entities["run"])
    assert result.state == "delivered"
    assert result.attributes["count"] == 2


async def test_durable_environment_result_shows_actual_outcome_without_description(hass, display):
    _, _, entities = display
    with patch(
        "custom_components.strava_activity_tools.environment_task",
        AsyncMock(
            return_value={
                "status": "updated",
                "changes": {"description": "PRIVATE NOTES"},
                "aqi": 40,
                "uv": 2,
            }
        ),
    ):
        result = await call_action(hass, "append_environment", dry_run=False)
    assert result["status"] == "done"
    state = hass.states.get(entities["run"])
    assert state.state == "updated"
    assert state.attributes["aqi"] == 40
    assert state.attributes["changes"] == {"description": "changed"}
    assert state.attributes["count"] == 1
    assert "PRIVATE" not in str(state.attributes)


async def test_failed_platform_unload_preserves_running_account(hass, display):
    from custom_components.strava_activity_tools import async_unload_entry

    entry, runtime, _ = display
    with patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)):
        assert not await async_unload_entry(hass, entry)
    assert hass.data[DOMAIN][entry.entry_id] is runtime
    assert not runtime.worker.closed


async def test_account_removal_deletes_display_history_and_entities(hass, display, hass_storage):
    entry, runtime, entities = display
    runtime.journal.record("update_activity", "9", False, {"status": "updated"})
    store = runtime.journal.store
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not runtime.journal.listeners
    assert hass.states.get(entities["run"]).state == "unavailable"
    assert "recent" not in hass.states.get(entities["run"]).attributes
    assert await store.async_load() is not None
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert store.key not in hass_storage
    assert hass.states.get(entities["run"]) is None


async def test_native_markdown_card_renders_real_history_and_empty_state(hass, display):
    from pathlib import Path

    import yaml
    from homeassistant.helpers.template import Template

    _, runtime, _ = display
    config = yaml.safe_load((Path(__file__).parents[1] / "examples/history-card.yaml").read_text())
    template = Template(config["content"], hass)
    assert "Nothing recorded yet" in template.async_render()
    runtime.journal.record(
        "update_activity",
        "9",
        True,
        {
            "status": "would_update",
            "changes": {"hide_from_home": True},
        },
    )
    runtime.journal.record(
        "append_environment",
        "9",
        False,
        {
            "status": "updated",
            "aqi": 40,
            "uv": 2,
        },
    )
    rendered = template.async_render()
    assert "https://www.strava.com/activities/9" in rendered
    assert "Would update" in rendered
    assert "Hide from feed: True" in rendered
    assert "AQI: 40" in rendered
    assert "UV: 2" in rendered
    await hass.config_entries.async_remove("tools")
    await hass.async_block_till_done()
    assert "No history entities found" in template.async_render()


async def test_display_history_expires_without_another_activity(hass, display, freezer):
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    _, runtime, entities = display
    runtime.journal.record("update_activity", "9", True, {"status": "would_update"})
    freezer.tick(8 * 86400)
    from homeassistant.util import dt as dt_util

    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get(entities["preview"]).state == "no_activity"
    assert hass.states.get(entities["preview"]).attributes["recent"] == []

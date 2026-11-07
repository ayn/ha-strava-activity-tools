"""Legacy-domain migration contracts with synthetic native HA stores only."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.strava_activity_tools.client import ActivityError
from custom_components.strava_activity_tools.jobs import DurableJobs
from custom_components.strava_activity_tools.migration import migrate_legacy_receipts
from custom_components.strava_activity_tools.worker import ScopedStore, job_store


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Load the actual config-flow implementation."""


@pytest.mark.parametrize("problem", [None, "foreign_owner", "foreign_source"])
async def test_native_migration_flow_checks_owner_and_copies_options(hass, problem):
    source = MockConfigEntry(domain="ha_strava", entry_id="source", unique_id="42")
    source.add_to_hass(hass)
    hass.data["ha_strava"] = {source.entry_id: SimpleNamespace(oauth_session=AsyncMock())}
    old = MockConfigEntry(
        domain="activity_tools",
        entry_id="legacy",
        disabled_by=ConfigEntryDisabler.USER,
        data={
            "source_entry_id": "other" if problem == "foreign_source" else "source",
            "athlete_id": "99" if problem == "foreign_owner" else "42",
        },
        options={"preview_only": False, "track_shoes": "g7"},
    )
    old.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        "strava_activity_tools", context={"source": "user"}
    )
    with patch("custom_components.strava_activity_tools.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"source_entry_id": "source", "athlete_id": "42", "legacy_entry_id": "legacy"},
        )
        await hass.async_block_till_done()
    if problem:
        assert result["type"] == "form"
        assert result["errors"] == {"legacy_entry_id": "legacy_unavailable"}
    else:
        assert result["type"] == "create_entry"
        assert result["result"].data["legacy_entry_id"] == "legacy"
        assert result["result"].options == {"preview_only": True, "track_shoes": "g7"}


@pytest.fixture
async def legacy(hass):
    scope = {"source_entry_id": "source", "athlete_id": "42"}
    old = MockConfigEntry(
        domain="activity_tools", entry_id="legacy", data=scope, disabled_by=ConfigEntryDisabler.USER
    )
    old.add_to_hass(hass)
    new = MockConfigEntry(
        domain="strava_activity_tools", entry_id="new", data={**scope, "legacy_entry_id": "legacy"}
    )
    new.add_to_hass(hass)
    old_store = ScopedStore(
        Store(hass, 1, "activity_tools.legacy.jobs", private=True, atomic_writes=True), scope
    )
    queue = DurableJobs(
        old_store,
        AsyncMock(return_value={"status": "delivered"}),
        AsyncMock(),
        lambda: dt_util.utcnow().timestamp(),
    )
    key = await queue.submit("fetch", "9")
    await queue.run_due()
    return old, new, old_store, key


async def test_completed_receipts_copy_without_mutating_legacy_store(hass, legacy):
    _, new, old_store, key = legacy
    before = await old_store.async_load()
    await migrate_legacy_receipts(hass, new)
    current = await job_store(hass, new.entry_id).async_load()
    assert current["jobs"][key] == before["jobs"][key]
    assert await old_store.async_load() == before
    await migrate_legacy_receipts(hass, new)
    assert await job_store(hass, new.entry_id).async_load() == current


@pytest.mark.parametrize("problem", ["enabled", "foreign_owner", "pending", "corrupt"])
async def test_unsafe_migration_does_not_touch_destination(hass, legacy, problem):
    old, new, old_store, key = legacy
    data = await old_store.async_load()
    if problem == "enabled":
        await hass.config_entries.async_set_disabled_by(old.entry_id, None)
    elif problem == "foreign_owner":
        data["owner"]["athlete_id"] = "99"
        await old_store.store.async_save(data)
    elif problem == "pending":
        data["jobs"][key]["status"] = "queued"
        await old_store.store.async_save(data)
    else:
        data["jobs"][key]["attempt"] = "bad"
        await old_store.store.async_save(data)
    with pytest.raises(ActivityError):
        await migrate_legacy_receipts(hass, new)
    assert await job_store(hass, new.entry_id).async_load() is None


@pytest.mark.parametrize("unsafe", [False, True])
async def test_setup_migrates_before_listening_and_clears_marker(hass, legacy, unsafe):
    from homeassistant.exceptions import ConfigEntryNotReady
    from pytest_homeassistant_custom_component.common import async_capture_events

    from custom_components.strava_activity_tools import (
        async_setup,
        async_setup_entry,
        async_unload_entry,
    )
    from custom_components.strava_activity_tools.const import DOMAIN, READY_EVENT, SOURCE_EVENT

    _, new, old_store, key = legacy
    if unsafe:
        data = await old_store.async_load()
        data["jobs"][key]["status"] = "queued"
        await old_store.store.async_save(data)
    hass.data["ha_strava"] = {"source": SimpleNamespace(oauth_session=AsyncMock())}
    await async_setup(hass, {})
    hass.config.components.add(DOMAIN)
    if unsafe:
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, new)
        assert new.entry_id not in hass.data[DOMAIN]
        assert "legacy_entry_id" in new.data
    else:
        async with new.setup_lock:
            assert await async_setup_entry(hass, new)
        assert "legacy_entry_id" not in new.data
        assert key in hass.data[DOMAIN][new.entry_id].worker.queue.jobs
    events = async_capture_events(hass, READY_EVENT)
    with patch(
        "custom_components.strava_activity_tools.client.ActivityClient.get", new_callable=AsyncMock
    ) as get:
        hass.bus.async_fire(SOURCE_EVENT, {"athlete_id": "42", "activity_id": "9"})
        await hass.async_block_till_done(wait_background_tasks=True)
        get.assert_not_called()
        assert not events
    if not unsafe:
        await async_unload_entry(hass, new)

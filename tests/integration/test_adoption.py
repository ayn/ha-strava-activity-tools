"""Advanced MCP handover keeps completed receipts without enabling writes."""

from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_strava_automation.adoption import async_adopt_activity_tools
from custom_components.ha_strava_automation.client import ActivityError
from custom_components.ha_strava_automation.jobs import DurableJobs

from .test_auth import account
from .test_inbox import MemoryStore


@pytest.fixture
async def adoption(hass, oauth_app, oauth_responses):
    target = account(hass, oauth_app)
    hass.config_entries.async_update_entry(
        target, data={**target.data, "migration_source_entry_id": "old-provider"}
    )
    oauth_responses()
    assert await hass.config_entries.async_setup(target.entry_id)
    source = MockConfigEntry(
        domain="strava_activity_tools",
        unique_id="42",
        data={"athlete_id": "42", "source_entry_id": "old-provider"},
        options={"track_shoes": "g123", "preview_only": False},
        disabled_by=ConfigEntryDisabler.USER,
    )
    source.add_to_hass(hass)
    disk = MemoryStore()
    queue = DurableJobs(
        disk,
        AsyncMock(return_value={"status": "updated"}),
        AsyncMock(),
        lambda: dt_util.utcnow().timestamp(),
    )
    key = await queue.submit(
        "mute", "123", {"filters": {"device_exact": "Synthetic collar"}, "dry_run": False}
    )
    await queue.run_due()
    snapshot = {**disk.data, "owner": {"athlete_id": "42", "source_entry_id": "old-provider"}}
    store = Store(
        hass, 1, f"strava_activity_tools.{source.entry_id}.jobs", private=True, atomic_writes=True
    )
    await store.async_save(snapshot)
    return target, source, store, key


async def test_adoption_is_idempotent_keeps_receipts_and_preview_lock(hass, adoption):
    target, source, store, key = adoption
    before = await store.async_load()
    await async_adopt_activity_tools(hass, target.entry_id, source.entry_id)
    await async_adopt_activity_tools(hass, target.entry_id, source.entry_id)
    assert target.runtime_data.worker.queue.jobs[key]["status"] == "done"
    assert len(target.runtime_data.worker.queue.jobs) == 1
    assert target.options["preview_only"] is True
    assert target.options["track_shoes"] == "g123"
    assert await store.async_load() == before


async def test_foreign_store_owner_is_never_imported(hass, adoption):
    target, source, store, _ = adoption
    data = await store.async_load()
    data["owner"]["athlete_id"] = "99"
    await store.async_save(data)
    with pytest.raises(ActivityError, match="legacy_owner_mismatch"):
        await async_adopt_activity_tools(hass, target.entry_id, source.entry_id)
    assert target.runtime_data.worker.queue.jobs == {}


async def test_enabled_source_is_never_copied_while_it_can_write(hass, adoption):
    target, source, _, _ = adoption
    source.disabled_by = None
    with pytest.raises(ActivityError, match="legacy_source_active"):
        await async_adopt_activity_tools(hass, target.entry_id, source.entry_id)
    assert not target.runtime_data.worker.queue.jobs


@pytest.mark.parametrize(
    "service,extra",
    [
        ("get_activity", {"activity_id": "123"}),
        (
            "update_activity",
            {"activity_id": "123", "changes": {"hide_from_home": True}, "dry_run": False},
        ),
        ("adopt_activity_tools_state", {"source_entry_id": "legacy"}),
    ],
)
async def test_nonadmin_cannot_read_write_or_adopt(
    hass, adoption, aioclient_mock, service, extra, hass_read_only_user
):
    from homeassistant.core import Context
    from homeassistant.exceptions import Unauthorized

    target, _, _, _ = adoption
    before = aioclient_mock.call_count
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            "ha_strava_automation",
            service,
            {"config_entry_id": target.entry_id, **extra},
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
            return_response=True,
        )
    assert aioclient_mock.call_count == before
    assert not target.runtime_data.worker.queue.jobs


async def test_ambiguous_running_legacy_receipt_requires_review(hass, adoption):
    target, source, store, key = adoption
    data = await store.async_load()
    data["jobs"][key]["status"] = "running"
    await store.async_save(data)
    with pytest.raises(ActivityError, match="legacy_jobs_need_review"):
        await async_adopt_activity_tools(hass, target.entry_id, source.entry_id)
    assert not target.runtime_data.worker.queue.jobs


async def test_target_must_be_in_preview_and_same_connection(hass, adoption):
    target, source, _, _ = adoption
    hass.config_entries.async_update_entry(target, options={"preview_only": False})
    with pytest.raises(ActivityError, match="legacy_target_not_preview"):
        await async_adopt_activity_tools(hass, target.entry_id, source.entry_id)
    hass.config_entries.async_update_entry(target, options={"preview_only": True})
    hass.config_entries.async_update_entry(source, data={**source.data, "source_entry_id": "other"})
    with pytest.raises(ActivityError, match="legacy_owner_mismatch"):
        await async_adopt_activity_tools(hass, target.entry_id, source.entry_id)

"""Marketplace entry removal must not force receipt loss or pending write imports."""

from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.ha_strava_automation.adoption import async_import_completed_jobs
from custom_components.ha_strava_automation.client import ActivityError
from custom_components.ha_strava_automation.migration import migration_store

from . import test_adoption

adoption = test_adoption.adoption


@pytest.fixture
async def terminal(hass, adoption):
    target, source, store, key = adoption
    data = await store.async_load()
    await hass.config_entries.async_remove(source.entry_id)
    await store.async_remove()
    await migration_store(hass, "old-provider").async_save(
        {
            "phase": "active",
            "athlete_id": "42",
            "client_id": "100",
            "target_entry_id": target.entry_id,
            "source_entry_id": "old-provider",
        }
    )
    return target, data["jobs"], key


async def test_terminal_receipts_survive_old_entry_removal_and_reload(hass, terminal):
    target, receipts, key = terminal
    result = await hass.services.async_call(
        "ha_strava_automation",
        "import_completed_jobs",
        {"config_entry_id": target.entry_id, "expected_athlete_id": "42", "receipts": receipts},
        blocking=True,
        return_response=True,
    )
    assert result["jobs"] == 1
    with patch.object(target.runtime_data.client, "get", AsyncMock()) as get:
        for _ in range(2):
            result = await async_import_completed_jobs(hass, target.entry_id, "42", receipts)
            assert result["jobs"] == 1
        get.assert_not_awaited()
    assert target.runtime_data.worker.queue.jobs[key]["status"] == "done"
    assert target.options["preview_only"] is True
    assert await hass.config_entries.async_reload(target.entry_id)
    assert target.runtime_data.worker.queue.jobs[key]["status"] == "done"


@pytest.mark.parametrize("status", ["queued", "running", "failed"])
async def test_nonterminal_receipts_never_create_work(hass, terminal, status):
    target, receipts, key = terminal
    receipts = deepcopy(receipts)
    receipts[key]["status"] = status
    with pytest.raises(ActivityError, match="invalid_terminal_receipts"):
        await async_import_completed_jobs(hass, target.entry_id, "42", receipts)
    assert not target.runtime_data.worker.queue.jobs


async def test_wrong_owner_or_migration_record_cannot_import(hass, terminal):
    target, receipts, _ = terminal
    with pytest.raises(ActivityError, match="legacy_owner_mismatch"):
        await async_import_completed_jobs(hass, target.entry_id, "99", receipts)
    await migration_store(hass, "old-provider").async_save({"phase": "active", "athlete_id": "99"})
    with pytest.raises(ActivityError, match="legacy_owner_mismatch"):
        await async_import_completed_jobs(hass, target.entry_id, "42", receipts)
    assert not target.runtime_data.worker.queue.jobs


async def test_hash_mismatch_does_not_reset_saved_state(hass, terminal):
    target, receipts, key = terminal
    receipts = deepcopy(receipts)
    receipts[key]["parameters"]["filters"]["device_exact"] = "Other"
    with pytest.raises(ActivityError, match="invalid_job_store"):
        await async_import_completed_jobs(hass, target.entry_id, "42", receipts)
    assert not target.runtime_data.worker.queue.jobs

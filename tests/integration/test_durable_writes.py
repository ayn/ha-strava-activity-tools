"""Generic writes and calendar processing retain semantic intents over restarts."""

from unittest.mock import AsyncMock

import pytest

from custom_components.ha_strava_automation.client import ActivityError
from custom_components.ha_strava_automation.jobs import DurableJobs

from .test_inbox import MemoryStore


async def test_guarded_update_is_restored_without_replaying_completed_write():
    store = MemoryStore()
    execute = AsyncMock(return_value={"status": "updated"})
    queue = DurableJobs(store, execute, AsyncMock(), lambda: 1800000000)
    key = await queue.submit(
        "update",
        "123",
        {
            "changes": {"workout_type": 3},
            "expected": {"sport_type": "Run", "workout_type": 0},
            "dry_run": False,
        },
    )
    await queue.run_due()
    restarted = DurableJobs(store, execute, AsyncMock(), lambda: 1800000010)
    await restarted.load()
    same = await restarted.submit(
        "update",
        "123",
        {
            "changes": {"workout_type": 3},
            "expected": {"sport_type": "Run", "workout_type": 0},
            "dry_run": False,
        },
    )
    assert same == key
    await restarted.run_due()
    execute.assert_awaited_once()


async def test_calendar_job_survives_restart_without_persisting_prescription_text():
    store = MemoryStore()
    execute = AsyncMock(return_value={"status": "unchanged"})
    first = DurableJobs(store, execute, AsyncMock(), lambda: 1800000000)
    await first.submit("workout", "123", {"calendar_entity": "calendar.training", "dry_run": False})
    assert "calendar.training" in str(store.data)
    assert set(next(iter(store.data["jobs"].values()))["parameters"]) == {
        "calendar_entity",
        "dry_run",
    }
    restarted = DurableJobs(store, execute, AsyncMock(), lambda: 1800000010)
    await restarted.load()
    await restarted.run_due()
    execute.assert_awaited_once()


@pytest.mark.parametrize(
    "parameters",
    [
        {"changes": {"private": False}},
        {"changes": {"workout_type": True}},
        {"changes": {"gear_id": "g１２３"}},
        {"changes": {"commute": True}, "dry_run": 1},
        {"changes": {"commute": True}, "expected": {"manual": "false"}},
    ],
)
async def test_invalid_write_intents_are_rejected_before_storage(parameters):
    store = MemoryStore()
    queue = DurableJobs(store, AsyncMock(), AsyncMock(), lambda: 1800000000)
    with pytest.raises(ActivityError):
        await queue.submit("update", "123", parameters)
    assert store.data is None

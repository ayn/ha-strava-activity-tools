"""Account-local, bounded preview and processing history, with no raw activity data."""

from unittest.mock import AsyncMock, patch

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.strava_activity_tools.const import DOMAIN
from custom_components.strava_activity_tools.journal import RunJournal, journal_store


def entry_for(hass, entry_id="tools", athlete="42"):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Synthetic athlete",
        entry_id=entry_id,
        data={"source_entry_id": "source", "athlete_id": athlete},
    )
    entry.add_to_hass(hass)
    return entry


async def test_preview_retains_useful_changes_without_private_payloads(hass):
    journal = RunJournal(hass, entry_for(hass))
    await journal.load()
    journal.record(
        "update_activity",
        "9",
        True,
        {
            "status": "would_update",
            "changes": {
                "hide_from_home": True,
                "description": "PRIVATE NOTES",
                "name": "PRIVATE TITLE",
            },
            "access_token": "SECRET",
            "start_latlng": [10, 20],
        },
    )
    record = journal.recent(True)[0]
    assert record["status"] == "would_update"
    assert record["activity_id"] == "9"
    assert record["changes"] == {
        "hide_from_home": True,
        "description": "changed",
        "name": "changed",
    }
    assert "PRIVATE" not in str(record)
    assert "SECRET" not in str(record)
    assert "start_latlng" not in record
    assert journal.recent(False) == []
    await journal.close()


async def test_history_is_bounded_persisted_and_expires(hass, freezer):
    entry = entry_for(hass)
    journal = RunJournal(hass, entry)
    await journal.load()
    for activity_id in range(1, 61):
        journal.record("update_activity", str(activity_id), False, {"status": "updated"})
    assert len(journal.recent(False)) == 25
    assert journal.recent(False)[0]["activity_id"] == "60"
    await journal.close()
    restored = RunJournal(hass, entry)
    await restored.load()
    assert restored.recent(False) == journal.recent(False)
    freezer.tick(8 * 86400)
    assert restored.recent(False) == []
    await restored.close()


async def test_history_owner_change_never_discloses_previous_account(hass):
    entry = entry_for(hass)
    journal = RunJournal(hass, entry)
    await journal.load()
    journal.record("update_activity", "9", False, {"status": "updated"})
    await journal.close()
    hass.config_entries.async_update_entry(entry, data={**entry.data, "athlete_id": "99"})
    restored = RunJournal(hass, entry)
    await restored.load()
    assert restored.recent(False) == []
    await restored.close()


async def test_untrusted_errors_and_invalid_activity_ids_are_not_disclosed(hass):
    journal = RunJournal(hass, entry_for(hass))
    await journal.load()
    journal.record("update_activity", "9", False, {"status": "failed", "error": "SECRET"})
    journal.record("update_activity", "token-in-url", False, {"status": "updated"})
    assert len(journal.recent(False)) == 1
    assert journal.recent(False)[0]["error"] == "internal_error"
    assert "SECRET" not in str(journal.recent(False))
    await journal.close()


async def test_history_save_failure_does_not_report_successful_strava_write_as_failed(hass, caplog):
    journal = RunJournal(hass, entry_for(hass))
    await journal.load()
    with patch.object(journal.store, "async_save", AsyncMock(side_effect=OSError("SECRET"))):
        journal.record("update_activity", "9", False, {"status": "updated"})
        await journal.close()
    assert journal.recent(False)[0]["status"] == "updated"
    assert "SECRET" not in caplog.text
    assert "history" in caplog.text.lower()


async def test_corrupt_history_is_discarded_without_blocking_jobs(hass):
    entry = entry_for(hass)
    await journal_store(hass, entry.entry_id).async_save({"records": "bad"})
    journal = RunJournal(hass, entry)
    await journal.load()
    assert journal.recent(False) == []
    await journal.close()


async def test_events_notify_listeners_and_snapshots_cannot_mutate_history(hass):
    journal = RunJournal(hass, entry_for(hass))
    await journal.load()
    calls = []
    unsubscribe = journal.listen(lambda: calls.append(True))
    journal.record("append_environment", "9", True, {"status": "would_update", "aqi": 40, "uv": 2})
    assert calls == [True]
    record = journal.recent(True)[0]
    assert record["aqi"] == 40
    assert record["uv"] == 2
    assert dt_util.parse_datetime(record["timestamp"]) is not None
    record["status"] = "tampered"
    assert journal.recent(True)[0]["status"] == "would_update"
    unsubscribe()
    await journal.close()


async def test_bad_display_listener_does_not_interrupt_successful_processing(hass, caplog):
    journal = RunJournal(hass, entry_for(hass))
    await journal.load()

    def broken_listener():
        raise RuntimeError("SECRET")

    journal.listen(broken_listener)
    journal.record("update_activity", "9", False, {"status": "updated"})
    assert journal.recent(False)[0]["status"] == "updated"
    assert "SECRET" not in caplog.text
    await journal.close()

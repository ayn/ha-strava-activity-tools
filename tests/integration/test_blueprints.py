"""Load shipping blueprint contracts through HA's actual automation engine."""

from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import SupportsResponse
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.ha_strava_automation.const import READY_EVENT

from . import test_pipeline

pipeline = test_pipeline.pipeline


@pytest.mark.parametrize(
    "name,inputs,status",
    [
        ("mute_activity", {"name_contains": "Synthetic"}, "would_update"),
        ("assign_gear", {"gear_id": "g123"}, "would_update"),
        ("append_environment", {}, "would_update"),
        ("process_workout", {"calendar_entity": "calendar.training"}, "suggested"),
    ],
)
async def test_blueprint_default_preview_is_visible_and_never_writes(
    hass, pipeline, name, inputs, status
):
    entry, _, _, activity, writes, _ = pipeline
    activity["start_latlng"] = [1.25, 2.5]
    folder = Path(hass.config.path("blueprints/automation/ha_strava_automation"))
    folder.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).parents[2] / "blueprints/automation" / f"{name}.yaml"
    (folder / source.name).write_text(source.read_text())

    async def calendar(_call):
        today = dt_util.utcnow()
        return {
            "calendar.training": {
                "events": [
                    {
                        "start": today.date().isoformat(),
                        "end": (today + timedelta(days=1)).date().isoformat(),
                        "summary": "4 x 400m",
                    }
                ]
            }
        }

    hass.services.async_register(
        "calendar", "get_events", calendar, supports_response=SupportsResponse.ONLY
    )
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": [
                {
                    "alias": "Synthetic blueprint preview",
                    "use_blueprint": {
                        "path": f"ha_strava_automation/{name}.yaml",
                        "input": {"config_entry_id": entry.entry_id, **inputs},
                    },
                }
            ]
        },
    )
    with patch(
        "custom_components.ha_strava_automation.actions.async_weather_readings",
        AsyncMock(return_value=[40, 0]),
    ):
        hass.bus.async_fire(
            READY_EVENT,
            {"config_entry_id": entry.entry_id, "activity_id": "123", "sport_type": "Run"},
        )
        await hass.async_block_till_done(wait_background_tasks=True)
    assert not writes
    records = entry.runtime_data.journal.recent(True)
    assert records and records[0]["status"] == status
    assert not entry.runtime_data.worker.queue.jobs

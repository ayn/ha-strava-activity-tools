"""Recorder lookups run on the recorder executor, never HA's event loop."""

from __future__ import annotations

from datetime import timedelta
from functools import partial

from homeassistant.components.recorder import get_instance, history
from homeassistant.core import HomeAssistant

from .client import ActivityError
from .core import parse_time
from .environment import reading_at


async def async_readings(
    hass: HomeAssistant,
    start: str,
    entities: list[str],
) -> list[float | None]:
    """Request the initial snapshot at activity start, not today's current states."""
    when = parse_time(start)
    if "recorder" not in hass.config.components:
        raise ActivityError("recorder_unavailable")
    result = await get_instance(hass).async_add_executor_job(
        partial(
            history.get_significant_states,
            hass,
            when,
            when + timedelta(seconds=1),
            entity_ids=entities,
            include_start_time_state=True,
            significant_changes_only=False,
        )
    )
    return [
        reading_at(
            [
                {"state": state.state, "last_updated": state.last_updated.isoformat()}
                for state in result.get(entity, [])
            ],
            start,
        )
        for entity in entities
    ]

"""Native, event-driven display entities for previews and processing history."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .journal import STATUSES, RunJournal


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    journal = hass.data[DOMAIN][entry.entry_id].journal
    async_add_entities([JournalSensor(entry, journal, preview) for preview in (True, False)])


class JournalSensor(SensorEntity):
    """Latest result with a bounded recent list, suitable for native dashboard cards."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = sorted(STATUSES | {"no_activity"})
    # Do not duplicate a sliding history list into every recorder state change.
    _unrecorded_attributes = frozenset({"recent"})

    def __init__(self, entry: ConfigEntry, journal: RunJournal, preview: bool) -> None:
        self.journal = journal
        self.preview = preview
        key = "latest_preview" if preview else "latest_run"
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title or "Strava Activity Tools",
            manufacturer="Strava Activity Tools",
            model="Activity automation companion",
            entry_type=DeviceEntryType.SERVICE,
        )
        self._attr_icon = "mdi:eye-outline" if preview else "mdi:history"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.journal.listen(self.async_write_ha_state))

    @property
    def native_value(self) -> str:
        records = self.journal.recent(self.preview)
        return records[0]["status"] if records else "no_activity"

    @property
    def extra_state_attributes(self) -> dict:
        records = self.journal.recent(self.preview)
        return {
            "stream": "previews" if self.preview else "runs",
            "recent": records,
            "count": len(records),
            **(records[0] if records else {}),
        }

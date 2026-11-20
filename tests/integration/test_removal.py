"""Native entry removal cannot delete another local account's callback."""

from unittest.mock import AsyncMock

from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_strava_automation.const import DOMAIN

from .test_auth import account


async def test_last_entry_removal_cleans_owned_callback_and_private_entry_stores(
    hass, oauth_app, oauth_responses
):
    entry = account(hass, oauth_app)
    oauth_responses()
    assert await hass.config_entries.async_setup(entry.entry_id)
    manager = entry.runtime_data.application
    manager.remove = AsyncMock()
    for suffix in ("jobs", "history", "inbox"):
        await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.{suffix}").async_save({"sentinel": True})
    assert await hass.config_entries.async_remove(entry.entry_id)
    manager.remove.assert_awaited_once()
    for suffix in ("jobs", "history", "inbox"):
        assert await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.{suffix}").async_load() is None


async def test_disabled_peer_preserves_shared_app_subscription(hass, oauth_app, oauth_responses):
    from homeassistant.config_entries import ConfigEntryDisabler

    entry = account(hass, oauth_app)
    oauth_responses()
    assert await hass.config_entries.async_setup(entry.entry_id)
    manager = entry.runtime_data.application
    manager.remove = AsyncMock()
    peer = MockConfigEntry(
        domain=DOMAIN,
        unique_id="99",
        data={"client_id": "100"},
        disabled_by=ConfigEntryDisabler.USER,
    )
    peer.add_to_hass(hass)
    assert await hass.config_entries.async_remove(entry.entry_id)
    manager.remove.assert_not_awaited()

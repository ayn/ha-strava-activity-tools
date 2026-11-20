"""Native options use this account's gear and preserve its preview guard."""

from unittest.mock import patch

import pytest
from homeassistant.data_entry_flow import FlowResultType, InvalidData

from .test_auth import account


@pytest.fixture
async def configured(hass, oauth_app, oauth_responses):
    entry = account(hass, oauth_app)
    oauth_responses(
        athlete_body={
            "id": 42,
            "shoes": [
                {"id": "g123", "name": "Synthetic track shoes", "primary": False},
                {"id": "g124", "name": "Synthetic daily shoes", "primary": True},
            ],
        }
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    return entry


async def test_options_select_only_own_gear_and_preserve_unmentioned_settings(hass, configured):
    entry = configured
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "track_shoes": "g123",
            "preview_only": False,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["track_shoes"] == "g123"
    assert entry.options["preview_only"] is False
    assert entry.options["webhook_enabled"] is False


async def test_foreign_shoe_rejected_without_changing_options(hass, configured):
    entry = configured
    before = dict(entry.options)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {"track_shoes": "g999", "preview_only": False}
        )
    assert entry.options == before


async def test_unavailable_gear_does_not_prevent_locking_writes(hass, configured):
    entry = configured
    hass.config_entries.async_update_entry(entry, options={**entry.options, "track_shoes": "g999"})
    await hass.config_entries.async_unload(entry.entry_id)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "preview_only": True,
            "track_shoes": "g999",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["preview_only"] is True
    assert entry.options["track_shoes"] == "g999"


async def test_reauth_button_targets_this_independent_entry(hass, configured):
    entry = configured
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch.object(entry, "async_start_reauth") as reauth:
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "preview_only": True,
                "reauthorize_strava": True,
            },
        )
    reauth.assert_called_once_with(hass)

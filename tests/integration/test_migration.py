"""Native source session handover with a synthetic source integration, not its code."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryDisabler, ConfigFlow
from homeassistant.helpers.config_entry_oauth2_flow import (
    LocalOAuth2Implementation,
    OAuth2Session,
    async_get_implementations,
)
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    mock_integration,
    mock_platform,
)

from custom_components.ha_strava_automation.migration import (
    MigrationError,
    async_pause_source,
    async_prepare_migration,
)

from .conftest import DOMAIN, GRANTED


@pytest.fixture
async def source(hass, oauth_app, oauth_responses):
    # A framework-only synthetic integration, with no third-party implementation.
    mock_integration(
        hass,
        MockModule(
            "ha_strava",
            async_setup_entry=AsyncMock(return_value=True),
            async_unload_entry=AsyncMock(return_value=True),
        ),
    )

    class SyntheticSourceFlow(ConfigFlow, domain="ha_strava"):
        VERSION = 1

    mock_platform(hass, "ha_strava.config_flow", SimpleNamespace(ConfigFlow=SyntheticSourceFlow))
    entry = MockConfigEntry(
        domain="ha_strava",
        unique_id="42",
        data={
            "token": {
                "access_token": "source-access",
                "refresh_token": "source-refresh",
                "expires_at": time.time() + 21600,
                "expires_in": 21600,
                "scope": GRANTED,
                "athlete": {"id": 42, "private": "discard"},
            },
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    session = OAuth2Session(hass, entry, oauth_app[1])
    hass.data["ha_strava"] = {entry.entry_id: SimpleNamespace(oauth_session=session)}
    oauth_responses()
    return entry


async def test_prepare_verifies_identity_keeps_source_live_and_never_exposes_secrets(hass, source):
    snapshot = await async_prepare_migration(hass, source.entry_id)
    assert snapshot.athlete_id == "42"
    assert snapshot.client_id == "100"
    assert source.disabled_by is None
    assert snapshot.data["token"]["refresh_token"] == "source-refresh"
    assert "athlete" not in snapshot.data["token"]
    assert "source-access" not in repr(snapshot)
    assert "discard" not in str(snapshot.data)


async def test_confirm_pauses_old_owner_before_target_can_refresh(hass, source):
    snapshot = await async_prepare_migration(hass, source.entry_id)
    await async_pause_source(hass, snapshot)
    assert source.disabled_by is ConfigEntryDisabler.USER
    assert source.data["token"]["refresh_token"] == "source-refresh"


async def test_wrong_athlete_fails_before_source_is_paused(
    hass, source, aioclient_mock, oauth_responses
):
    aioclient_mock.clear_requests()
    oauth_responses(athlete=99)
    with pytest.raises(MigrationError, match="wrong_account"):
        await async_prepare_migration(hass, source.entry_id)
    assert source.disabled_by is None


async def test_unknown_permissions_do_not_invent_write_grants(hass, source):
    token = {k: v for k, v in source.data["token"].items() if k != "scope"}
    hass.config_entries.async_update_entry(source, data={"token": token})
    with pytest.raises(MigrationError, match="migration_scope_unknown"):
        await async_prepare_migration(hass, source.entry_id)
    assert source.disabled_by is None


async def test_existing_target_prevents_duplicate_handover(hass, source):
    target = MockConfigEntry(domain=DOMAIN, unique_id="42", data={"sentinel": "keep"})
    target.add_to_hass(hass)
    with pytest.raises(MigrationError, match="already_configured"):
        await async_prepare_migration(hass, source.entry_id)
    assert source.disabled_by is None
    assert target.data == {"sentinel": "keep"}


async def test_arbitrary_integration_cannot_supply_oauth_credentials(hass, source):
    unrelated = MockConfigEntry(domain="other_service", unique_id="42", data={})
    unrelated.add_to_hass(hass)
    with pytest.raises(MigrationError, match="unsupported_source"):
        await async_prepare_migration(hass, unrelated.entry_id)
    assert source.disabled_by is None


async def test_active_companion_blocks_handover_before_pause(hass, source):
    companion = MockConfigEntry(
        domain="strava_activity_tools",
        data={"source_entry_id": source.entry_id, "athlete_id": "42"},
    )
    companion.add_to_hass(hass)
    with pytest.raises(MigrationError, match="dependent_integration_active"):
        await async_prepare_migration(hass, source.entry_id)
    assert source.disabled_by is None


async def test_disabled_companion_that_failed_to_unload_still_blocks(hass, source):
    from homeassistant.config_entries import ConfigEntryState

    companion = MockConfigEntry(
        domain="strava_activity_tools",
        data={"source_entry_id": source.entry_id, "athlete_id": "42"},
        disabled_by=ConfigEntryDisabler.USER,
    )
    companion.add_to_hass(hass)
    companion._async_set_state(hass, ConfigEntryState.LOADED, None)
    try:
        with pytest.raises(MigrationError, match="dependent_integration_active"):
            await async_prepare_migration(hass, source.entry_id)
    finally:
        companion._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)
    assert source.disabled_by is None


async def test_native_flow_requires_confirmation_then_transfers_same_app(hass, source):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "migrate"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"source_entry_id": source.entry_id}
    )
    assert result["step_id"] == "migration_confirm"
    assert source.disabled_by is None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"confirm_pause": False}
    )
    assert result["errors"] == {"base": "migration_confirmation_required"}
    assert source.disabled_by is None
    with patch("custom_components.ha_strava_automation.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"confirm_pause": True}
        )
        await hass.async_block_till_done()
    assert source.disabled_by is ConfigEntryDisabler.USER
    assert result["result"].unique_id == "42"
    assert result["data"]["client_id"] == "100"
    assert result["data"]["token"]["refresh_token"] == "source-refresh"
    assert result["options"]["preview_only"] is True


async def test_paused_handover_can_resume_after_runtime_is_lost(hass, source):
    snapshot = await async_prepare_migration(hass, source.entry_id)
    await async_pause_source(hass, snapshot)
    hass.data["ha_strava"].clear()
    resumed = await async_prepare_migration(hass, source.entry_id)
    assert resumed.athlete_id == snapshot.athlete_id
    assert resumed.data["token"]["refresh_token"] == "source-refresh"
    assert source.disabled_by is ConfigEntryDisabler.USER


async def test_migration_registers_new_native_credentials_without_a_new_strava_app(hass, source):
    implementation = LocalOAuth2Implementation(
        hass,
        "synthetic_old_app",
        "200",
        "synthetic-original-secret",
        "https://www.strava.com/oauth/authorize",
        "https://www.strava.com/oauth/token",
    )
    hass.data["ha_strava"][source.entry_id].oauth_session = OAuth2Session(
        hass, source, implementation
    )
    snapshot = await async_prepare_migration(hass, source.entry_id)
    assert snapshot.client_id == "200"
    await async_pause_source(hass, snapshot)
    implementations = await async_get_implementations(hass, DOMAIN)
    target = implementations[snapshot.data["auth_implementation"]]
    assert target.client_id == "200"
    assert target.client_secret == "synthetic-original-secret"
    assert "synthetic-original-secret" not in repr(snapshot)


async def test_shared_source_app_must_not_lose_other_athletes_callback(hass, source, oauth_app):
    other = MockConfigEntry(domain="ha_strava", unique_id="99", data={})
    other.add_to_hass(hass)
    assert await hass.config_entries.async_setup(other.entry_id)
    hass.data["ha_strava"][other.entry_id] = SimpleNamespace(
        oauth_session=OAuth2Session(hass, other, oauth_app[1])
    )
    with pytest.raises(MigrationError, match="shared_application_migration"):
        await async_prepare_migration(hass, source.entry_id)
    assert source.disabled_by is None
    assert other.disabled_by is None


async def test_unknown_remote_callback_is_not_implicitly_adopted(
    hass, source, aioclient_mock, oauth_responses
):
    aioclient_mock.clear_requests()
    oauth_responses()
    aioclient_mock.clear_requests()
    aioclient_mock.get("https://www.strava.com/api/v3/athlete", json={"id": 42})
    aioclient_mock.get(
        "https://www.strava.com/api/v3/push_subscriptions",
        json=[{"id": 12, "callback_url": "https://another.example.test/callback"}],
    )
    with pytest.raises(MigrationError, match="migration_subscription_unknown"):
        await async_prepare_migration(hass, source.entry_id)
    assert source.disabled_by is None


async def test_verified_source_callback_can_be_recorded_for_explicit_handover(
    hass, source, aioclient_mock
):
    callback = "https://old.example.test/api/strava/webhook"
    hass.config_entries.async_update_entry(source, data={**source.data, "callback_url": callback})
    aioclient_mock.clear_requests()
    aioclient_mock.get("https://www.strava.com/api/v3/athlete", json={"id": 42})
    aioclient_mock.get(
        "https://www.strava.com/api/v3/push_subscriptions",
        json=[{"id": 12, "callback_url": callback}],
    )
    result = await async_prepare_migration(hass, source.entry_id)
    assert result.data["migration_remote_subscription"] == {"id": "12", "callback_url": callback}
    assert source.disabled_by is None

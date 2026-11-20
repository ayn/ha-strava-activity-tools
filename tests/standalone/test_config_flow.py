"""Behavioral OAuth contracts authored from HA/Strava docs, independently."""

from unittest.mock import patch

import pytest
from homeassistant.components.application_credentials import (
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.config_entry_oauth2_flow import async_get_implementations
from pytest_homeassistant_custom_component.common import MockConfigEntry
from yarl import URL

from .conftest import ATHLETE_URL, DOMAIN, GRANTED, finish_auth, start_auth


async def test_native_flow_verifies_identity_and_defaults_preview(
    hass, oauth_app, oauth_responses, aioclient_mock
):
    oauth_responses()
    result = await start_auth(hass, oauth_app)
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    with patch("custom_components.ha_strava_automation.async_setup_entry", return_value=True):
        result = await finish_auth(hass, result)
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.unique_id == "42"
    assert entry.data["athlete_id"] == "42"
    assert entry.options == {"preview_only": True}
    assert "athlete" not in entry.data["token"]
    assert "discard@example.test" not in str(entry.data)
    assert aioclient_mock.mock_calls[-1][0] == "GET"
    assert str(aioclient_mock.mock_calls[-1][1]) == ATHLETE_URL
    assert aioclient_mock.mock_calls[-1][3]["Authorization"] == "Bearer test-access-token"


async def test_authorization_requests_explicit_feature_scopes(hass, oauth_app):
    result = await start_auth(hass, oauth_app)
    query = URL(result["url"]).query
    assert query["client_id"] == "100"
    assert query["response_type"] == "code"
    assert "state" in query
    assert set(query["scope"].split(",")) == {
        "read",
        "activity:read_all",
        "activity:write",
        "profile:read_all",
    }


async def test_no_activity_scope_does_not_create_account(hass, oauth_app, oauth_responses):
    oauth_responses(scopes="read")
    result = await finish_auth(hass, await start_auth(hass, oauth_app), scopes="read")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "missing_activity_scope"
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.parametrize("scopes", [None, [], True, "", "ａｃｔｉｖｉｔｙ:read", "x" * 1025])
async def test_malformed_scopes_fail_closed(hass, oauth_app, oauth_responses, scopes):
    oauth_responses(scopes=scopes)
    result = await finish_auth(hass, await start_auth(hass, oauth_app), scopes)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "missing_activity_scope"
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.parametrize("athlete", [True, False, 0, -1, "42", "４２", 1.5, 2**63])
async def test_invalid_api_identity_does_not_create_account(
    hass, oauth_app, oauth_responses, athlete
):
    oauth_responses(athlete=athlete)
    result = await finish_auth(hass, await start_auth(hass, oauth_app))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_account"
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.parametrize("body", [[], "not an athlete", {"name": "Missing ID"}])
async def test_non_object_or_missing_identity_is_rejected(hass, oauth_app, oauth_responses, body):
    oauth_responses(athlete_body=body)
    result = await finish_auth(hass, await start_auth(hass, oauth_app))
    assert result["reason"] == "invalid_account"
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_authenticated_api_overrides_token_response_identity(
    hass, oauth_app, oauth_responses
):
    oauth_responses(athlete=99, athlete_body={"id": 42}, scopes="activity:read")
    with patch("custom_components.ha_strava_automation.async_setup_entry", return_value=True):
        result = await finish_auth(hass, await start_auth(hass, oauth_app), "activity:read")
        await hass.async_block_till_done()
    assert result["result"].unique_id == "42"
    assert result["data"]["granted_scopes"] == ["activity:read"]
    assert result["options"] == {"preview_only": True}


@pytest.mark.parametrize(
    "status,reason",
    [
        (401, "invalid_auth"),
        (403, "invalid_auth"),
        (429, "cannot_connect"),
        (500, "cannot_connect"),
    ],
)
async def test_athlete_fetch_failure_creates_no_entry(
    hass, oauth_app, oauth_responses, status, reason
):
    oauth_responses(athlete_status=status)
    result = await finish_auth(hass, await start_auth(hass, oauth_app))
    assert result["reason"] == reason
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_duplicate_athlete_does_not_update_existing_account(hass, oauth_app, oauth_responses):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="42", data={"sentinel": "original"})
    entry.add_to_hass(hass)
    oauth_responses()
    result = await finish_auth(hass, await start_auth(hass, oauth_app))
    assert result["reason"] == "already_configured"
    assert entry.data == {"sentinel": "original"}
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def reauth(hass, entry, scopes=GRANTED):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reauth", "entry_id": entry.entry_id}, data=dict(entry.data)
    )
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    return await finish_auth(hass, result, scopes)


async def test_reauth_preserves_entry_options_and_custom_data(hass, oauth_app, oauth_responses):
    key, _ = oauth_app
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="42",
        data={
            "auth_implementation": key,
            "athlete_id": "42",
            "client_id": "100",
            "sentinel": "keep",
        },
        options={"preview_only": False, "track_shoes": "g123"},
    )
    entry.add_to_hass(hass)
    oauth_responses()
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await reauth(hass, entry)
    assert result["reason"] == "reauth_successful"
    assert entry.unique_id == "42"
    assert entry.options == {"preview_only": False, "track_shoes": "g123"}
    assert entry.data["sentinel"] == "keep"
    assert entry.data["token"]["refresh_token"] == "test-refresh-token"
    reload.assert_called_once_with(entry.entry_id)


async def test_reauth_wrong_athlete_cannot_replace_account(hass, oauth_app, oauth_responses):
    key, _ = oauth_app
    original = {"auth_implementation": key, "athlete_id": "42", "client_id": "100"}
    entry = MockConfigEntry(domain=DOMAIN, unique_id="42", data=original)
    entry.add_to_hass(hass)
    oauth_responses(athlete=99)
    result = await reauth(hass, entry)
    assert result["reason"] == "wrong_account"
    assert entry.data == original
    assert entry.unique_id == "42"


async def test_reauth_cannot_silently_change_application(hass, oauth_app, oauth_responses):
    key, _ = oauth_app
    original = {"auth_implementation": key, "athlete_id": "42", "client_id": "200"}
    entry = MockConfigEntry(domain=DOMAIN, unique_id="42", data=original)
    entry.add_to_hass(hass)
    oauth_responses()
    result = await reauth(hass, entry)
    assert result["reason"] == "wrong_application"
    assert entry.data == original


async def test_existing_other_integration_does_not_block_own_account(
    hass, oauth_app, oauth_responses
):
    old = MockConfigEntry(domain="ha_strava", unique_id="42", data={"sentinel": "untouched"})
    old.add_to_hass(hass)
    oauth_responses()
    with patch("custom_components.ha_strava_automation.async_setup_entry", return_value=True):
        result = await finish_auth(hass, await start_auth(hass, oauth_app))
        await hass.async_block_till_done()
    assert result["result"].unique_id == "42"
    assert old.data == {"sentinel": "untouched"}
    assert len(hass.config_entries.async_entries("ha_strava")) == 1


async def test_denied_consent_leaves_no_entry(hass, oauth_app, aioclient_mock):
    result = await start_auth(hass, oauth_app)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"error": "access_denied"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["reason"] == "user_rejected_authorize"
    assert aioclient_mock.call_count == 0
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_separate_apps_authorize_separate_accounts(
    hass, oauth_app, oauth_responses, aioclient_mock
):
    oauth_responses()
    with patch("custom_components.ha_strava_automation.async_setup_entry", return_value=True):
        first = await finish_auth(hass, await start_auth(hass, oauth_app))
        await hass.async_block_till_done()
        await async_import_client_credential(
            hass,
            DOMAIN,
            ClientCredential("200", "other-test-secret", "Other app"),
            auth_domain="other_app",
        )
        implementations = await async_get_implementations(hass, DOMAIN)
        other = next((k, v) for k, v in implementations.items() if v.client_id == "200")
        aioclient_mock.clear_requests()
        oauth_responses(athlete=99)
        second = await finish_auth(hass, await start_auth(hass, other))
        await hass.async_block_till_done()
    assert first["result"].unique_id == "42"
    assert second["result"].unique_id == "99"
    assert first["data"]["client_id"] == "100"
    assert second["data"]["client_id"] == "200"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2

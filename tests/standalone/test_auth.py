"""Native OAuth refresh, rejection and actual config-entry lifecycle contracts."""

import asyncio
import time
from unittest.mock import patch

import pytest
from aiohttp import ClientConnectionError, web
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import (
    OAuth2TokenRequestConnectionError,
    OAuth2TokenRequestReauthError,
)
from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import ATHLETE_URL, DOMAIN, GRANTED, TOKEN_URL, finish_auth, start_auth


def account(hass, oauth_app, *, expired=False, athlete="42"):
    """A synthetic persisted account from this integration's own flow contract."""
    key, _ = oauth_app
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=athlete,
        data={
            "auth_implementation": key,
            "client_id": "100",
            "athlete_id": athlete,
            "granted_scopes": GRANTED.split(","),
            "token": {
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "expires_at": 0 if expired else time.time() + 21600,
                "expires_in": 21600,
                "scope": GRANTED,
            },
        },
        options={"preview_only": True},
    )
    entry.add_to_hass(hass)
    return entry


async def test_concurrent_refresh_rotates_and_persists_once(
    hass, oauth_app, oauth_responses, aioclient_mock
):
    entry = account(hass, oauth_app, expired=True)
    oauth_responses()
    session = OAuth2Session(hass, entry, oauth_app[1])
    await asyncio.gather(*(session.async_ensure_token_valid() for _ in range(5)))
    posts = [call for call in aioclient_mock.mock_calls if call[0] == "POST"]
    assert len(posts) == 1
    assert posts[0][2]["grant_type"] == "refresh_token"
    assert posts[0][2]["refresh_token"] == "old-refresh"
    assert posts[0][2]["client_id"] == "100"
    assert entry.data["token"]["refresh_token"] == "test-refresh-token"
    assert entry.data["token"]["access_token"] == "test-access-token"
    assert session.valid_token
    # A session reconstructed after restart reads the rotated credentials.
    reloaded = OAuth2Session(hass, entry, oauth_app[1])
    await reloaded.async_ensure_token_valid()
    assert reloaded.token["refresh_token"] == "test-refresh-token"
    assert len(aioclient_mock.mock_calls) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"access_token": ""},
        {"access_token": True},
        {"refresh_token": None},
        {"refresh_token": ""},
        {"expires_in": True},
        {"expires_in": -1},
        {"expires_in": 1.5},
        {"expires_in": "21600"},
        {"expires_in": 999999999},
        {"token_type": "Basic"},
    ],
)
async def test_unusable_refresh_cannot_extend_stale_credentials(
    hass, oauth_app, oauth_responses, changes
):
    entry = account(hass, oauth_app, expired=True)
    old = dict(entry.data["token"])
    oauth_responses(token_changes=changes)
    with pytest.raises(OAuth2TokenRequestConnectionError):
        await OAuth2Session(hass, entry, oauth_app[1]).async_ensure_token_valid()
    assert entry.data["token"] == old


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_rejected_refresh_requests_reauth_without_overwriting_tokens(
    hass, oauth_app, aiohttp_server, socket_enabled, status, caplog
):
    entry = account(hass, oauth_app, expired=True)

    async def reject(request):
        form = await request.post()
        assert form["refresh_token"] == "old-refresh"
        return web.json_response({"message": "Bad Request test-access-secret"}, status=status)

    app = web.Application()
    app.router.add_post("/token", reject)
    server = await aiohttp_server(app)
    oauth_app[1].token_url = str(server.make_url("/token"))
    with patch.object(entry, "async_start_reauth_if_available") as reauth:
        with pytest.raises(OAuth2TokenRequestReauthError):
            await OAuth2Session(hass, entry, oauth_app[1]).async_ensure_token_valid()
    reauth.assert_called_once_with(hass)
    assert entry.data["token"]["refresh_token"] == "old-refresh"
    assert "test-access-secret" not in caplog.text


@pytest.mark.parametrize("status", [302, 429, 500, 503])
async def test_transient_refresh_is_not_a_reauth_request(hass, oauth_app, aioclient_mock, status):
    entry = account(hass, oauth_app, expired=True)
    aioclient_mock.post(TOKEN_URL, status=status, text="provider body must stay private")
    with patch.object(entry, "async_start_reauth_if_available") as reauth:
        with pytest.raises(OAuth2TokenRequestConnectionError):
            await OAuth2Session(hass, entry, oauth_app[1]).async_ensure_token_valid()
    reauth.assert_not_called()
    assert entry.data["token"]["expires_at"] == 0


async def test_runtime_setup_unload_reload_uses_native_session(
    hass, oauth_app, oauth_responses, aioclient_mock
):
    entry = account(hass, oauth_app)
    oauth_responses()
    assert await hass.config_entries.async_setup(entry.entry_id)
    first = entry.runtime_data.session
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.athlete_id == "42"
    assert "ha_strava" not in hass.config.components
    assert not hass.services.has_service(DOMAIN, "update_activity")
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not hasattr(entry, "runtime_data")
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.runtime_data.session is not first
    assert not any(call[0] != "GET" for call in aioclient_mock.mock_calls)


async def test_runtime_rejects_swapped_account_before_exposing_session(
    hass, oauth_app, oauth_responses
):
    entry = account(hass, oauth_app)
    oauth_responses(athlete=99)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is not ConfigEntryState.LOADED
    assert not hasattr(entry, "runtime_data")
    assert entry.data["athlete_id"] == "42"


async def test_refresh_outage_during_setup_is_retryable(hass, oauth_app, aioclient_mock):
    entry = account(hass, oauth_app, expired=True)
    aioclient_mock.post(TOKEN_URL, status=503, text="temporarily down")
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not hasattr(entry, "runtime_data")


async def test_real_http_callback_uses_token_scopes_and_signed_state(
    hass, oauth_app, oauth_responses, hass_client_no_auth
):
    from yarl import URL

    oauth_responses()
    result = await start_auth(hass, oauth_app)
    state = URL(result["url"]).query["state"]
    client = await hass_client_no_auth()
    with patch("custom_components.ha_strava_automation.async_setup_entry", return_value=True):
        response = await client.get(
            "/auth/external/callback",
            params={
                "code": "single-use-code",
                "state": state,
                "scope": "read",  # Query scope is not trusted as granted capability.
            },
        )
        assert response.status == 200
        created = await hass.config_entries.flow.async_configure(result["flow_id"])
        await hass.async_block_till_done()
    assert created["result"].unique_id == "42"
    assert "activity:write" in created["data"]["granted_scopes"]


async def test_real_http_callback_rejects_forged_state(
    hass, oauth_app, hass_client_no_auth, aioclient_mock
):
    await start_auth(hass, oauth_app)
    client = await hass_client_no_auth()
    response = await client.get(
        "/auth/external/callback",
        params={
            "code": "forged-code",
            "state": "forged-state",
        },
    )
    assert response.status == 400
    assert aioclient_mock.call_count == 0
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_missing_native_credentials_is_actionable(hass, oauth_app):
    entry = account(hass, oauth_app)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "auth_implementation": "removed-application"}
    )
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert "authentication method" in entry.reason
    assert not hasattr(entry, "runtime_data")


async def test_refreshed_scope_loss_cannot_reuse_stale_permissions(
    hass, oauth_app, oauth_responses
):
    entry = account(hass, oauth_app, expired=True)
    oauth_responses(scopes="read")
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is not ConfigEntryState.LOADED
    assert not hasattr(entry, "runtime_data")


@pytest.mark.parametrize(
    "status,expected", [(401, ConfigEntryState.SETUP_ERROR), (503, ConfigEntryState.SETUP_RETRY)]
)
async def test_identity_outage_and_revocation_have_different_recovery(
    hass, oauth_app, oauth_responses, status, expected
):
    entry = account(hass, oauth_app)
    oauth_responses(athlete_status=status)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is expected
    assert not hasattr(entry, "runtime_data")


async def test_changed_app_credentials_cannot_bind_runtime(hass, oauth_app, oauth_responses):
    entry = account(hass, oauth_app)
    hass.config_entries.async_update_entry(entry, data={**entry.data, "client_id": "200"})
    oauth_responses()
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert not hasattr(entry, "runtime_data")


@pytest.mark.parametrize("endpoint", [ATHLETE_URL, TOKEN_URL])
async def test_connection_failure_does_not_log_raw_exception(
    hass, oauth_app, aioclient_mock, endpoint, caplog
):
    error = ClientConnectionError("must-not-log-access-token")
    if endpoint == TOKEN_URL:
        aioclient_mock.post(TOKEN_URL, exc=error)
    else:
        aioclient_mock.post(
            TOKEN_URL,
            json={
                "access_token": "test-access",
                "refresh_token": "test-refresh",
                "expires_in": 21600,
                "scope": GRANTED,
            },
        )
        aioclient_mock.get(ATHLETE_URL, exc=error)
    result = await finish_auth(hass, await start_auth(hass, oauth_app))
    assert result["reason"] in ("cannot_connect", "oauth_failed")
    assert "must-not-log-access-token" not in caplog.text
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.parametrize("body", [[], "not a token", {"access_token": "do-not-log"}])
async def test_malformed_token_exchange_never_creates_entry_or_logs_body(
    hass, oauth_app, aioclient_mock, body, caplog
):
    aioclient_mock.post(TOKEN_URL, json=body)
    result = await finish_auth(hass, await start_auth(hass, oauth_app))
    assert result["reason"] == "oauth_failed"
    assert not hass.config_entries.async_entries(DOMAIN)
    assert "do-not-log" not in caplog.text

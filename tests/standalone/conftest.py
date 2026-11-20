"""Fresh independent OAuth fixtures; no other integration imports or credentials."""

from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest
from homeassistant.components.application_credentials import (
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.helpers.config_entry_oauth2_flow import async_get_implementations
from homeassistant.setup import async_setup_component

import custom_components

# The prototype is deliberately outside HACS' released component directory.
# Mount only our development source into this test process's namespace.
_development = str(Path(__file__).resolve().parents[2] / "development/custom_components")
if _development not in custom_components.__path__:
    custom_components.__path__.append(_development)

from custom_components.ha_strava_automation.const import DOMAIN  # noqa: E402

TOKEN_URL = "https://www.strava.com/oauth/token"
ATHLETE_URL = "https://www.strava.com/api/v3/athlete"
GRANTED = "read,activity:read_all,activity:write,profile:read_all"


@pytest.fixture(autouse=True)
def standalone_custom_integrations(enable_custom_integrations):
    """Exercise real HA discovery for the independent domain."""


@pytest.fixture
async def oauth_app(hass):
    """Import synthetic Application Credentials through the native HA API."""
    assert await async_setup_component(hass, "application_credentials", {})
    await async_import_client_credential(
        hass, DOMAIN, ClientCredential("100", "test-client-secret", "Test application")
    )
    implementations = await async_get_implementations(hass, DOMAIN)
    assert len(implementations) == 1
    key, implementation = next(iter(implementations.items()))
    with patch(
        "homeassistant.helpers.config_entry_oauth2_flow.LocalOAuth2Implementation.redirect_uri",
        new_callable=PropertyMock,
        return_value="https://example.test/auth/external/callback",
    ):
        yield key, implementation


@pytest.fixture
def oauth_responses(aioclient_mock):
    """Only synthetic provider responses; no production network requests."""

    def register(
        *,
        athlete=42,
        scopes=GRANTED,
        token_changes=None,
        athlete_body=None,
        token_status=200,
        athlete_status=200,
    ):
        token = {
            "access_token": "test-access-token",
            "refresh_token": "test-refresh-token",
            "expires_in": 21600,
            "token_type": "Bearer",
            "scope": scopes,
            "athlete": {"id": athlete, "email": "discard@example.test"},
        }
        token.update(token_changes or {})
        aioclient_mock.post(
            TOKEN_URL,
            json=token,
            status=token_status,
        )
        aioclient_mock.get(
            ATHLETE_URL,
            json=athlete_body if athlete_body is not None else {"id": athlete},
            status=athlete_status,
        )

    return register


async def start_auth(hass, oauth_app):
    """Walk the actual HA user and implementation selection flow."""
    key, _ = oauth_app
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    if result["type"].value == "menu":
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "new_account"}
        )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"implementation": key}
    )


async def finish_auth(hass, result, scopes=GRANTED):
    """Complete external authorization and native token exchange."""
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "code": "synthetic-single-use-code",
            "scope": scopes,
            "state": {"redirect_uri": "https://example.test/auth/external/callback"},
        },
    )
    return await hass.config_entries.flow.async_configure(result["flow_id"])

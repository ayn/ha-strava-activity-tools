"""Native HA credentials; endpoints come from Strava's authentication docs."""

from homeassistant.components.application_credentials import AuthorizationServer, ClientCredential
from homeassistant.core import HomeAssistant

from .auth import StravaOAuthImplementation
from .const import AUTHORIZE_URL, TOKEN_URL


async def async_get_authorization_server(hass: HomeAssistant) -> AuthorizationServer:
    """Use Home Assistant's supported OAuth implementation."""
    return AuthorizationServer(authorize_url=AUTHORIZE_URL, token_url=TOKEN_URL)


async def async_get_auth_implementation(
    hass: HomeAssistant, auth_domain: str, credential: ClientCredential
) -> StravaOAuthImplementation:
    """Each native Application Credential owns its independent Strava app."""
    return StravaOAuthImplementation(
        hass, auth_domain, credential, await async_get_authorization_server(hass)
    )

"""Independent Strava authentication foundation; no activity processing yet."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.config_entry_oauth2_flow import (
    OAuth2Session,
    async_get_config_entry_implementation,
)

from .auth import (
    AccountVerificationError,
    AuthRuntime,
    async_fetch_athlete,
    granted_scopes,
    has_activity_access,
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Own a native session and verify identity before exposing a runtime."""
    implementation = await async_get_config_entry_implementation(hass, entry)
    session = OAuth2Session(hass, entry, implementation)
    await session.async_ensure_token_valid()
    if not has_activity_access(granted_scopes(session.token.get("scope"))):
        raise ConfigEntryAuthFailed("missing_activity_scope")
    try:
        athlete_id = await async_fetch_athlete(hass, session.token["access_token"])
    except AccountVerificationError as err:
        if str(err) == "cannot_connect":
            raise ConfigEntryNotReady("Account verification temporarily unavailable") from None
        raise ConfigEntryAuthFailed("Account verification failed") from None
    if athlete_id != entry.unique_id or athlete_id != entry.data.get("athlete_id"):
        raise ConfigEntryAuthFailed("Authenticated athlete does not match configured account")
    if implementation.client_id != entry.data.get("client_id"):
        raise ConfigEntryAuthFailed("Application credentials do not match configured account")
    entry.runtime_data = AuthRuntime(session, athlete_id, implementation.client_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Release the local runtime without revoking tokens or touching subscriptions."""
    entry.runtime_data = None
    return True

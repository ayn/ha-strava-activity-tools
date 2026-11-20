"""Independent consent, verified account identity and reauth via native HA flows."""

import logging
from typing import Any

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.helpers.config_entry_oauth2_flow import AbstractOAuth2FlowHandler

from .auth import (
    REQUESTED_SCOPES,
    AccountVerificationError,
    async_fetch_athlete,
    granted_scopes,
    has_activity_access,
)
from .const import DOMAIN


class StravaAutomationFlow(AbstractOAuth2FlowHandler, domain=DOMAIN):
    """OAuth tokens belong to this integration, independently of any companion."""

    DOMAIN = DOMAIN
    VERSION = 1

    @property
    def logger(self) -> logging.Logger:
        """Return the integration logger."""
        return logging.getLogger(__name__)

    @property
    def extra_authorize_data(self) -> dict[str, str]:
        """Explain requested scopes in the setup UI; allow declined optional writes."""
        return {"scope": ",".join(REQUESTED_SCOPES), "approval_prompt": "force"}

    async def async_oauth_create_entry(self, data: dict) -> dict:
        """Bind tokens only to the authenticated athlete, preserving reauth options."""
        # HA's native callback forwards state/code, not the scope query parameter.
        # Use the authenticated token exchange's documented scope response.
        scopes = granted_scopes(data["token"].get("scope"))
        if not has_activity_access(scopes):
            return self.async_abort(reason="missing_activity_scope")
        try:
            athlete_id = await async_fetch_athlete(self.hass, data["token"]["access_token"])
        except AccountVerificationError as err:
            return self.async_abort(reason=str(err))
        await self.async_set_unique_id(athlete_id)
        data = {
            **data,
            "athlete_id": athlete_id,
            "client_id": self.flow_impl.client_id,
            "granted_scopes": scopes,
        }
        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            entry = self._get_reauth_entry()
            if entry.data["client_id"] != data["client_id"]:
                return self.async_abort(reason="wrong_application")
            return self.async_update_reload_and_abort(entry, data_updates=data)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"Strava ({athlete_id})", data=data, options={"preview_only": True}
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> dict:
        """Ask before starting browser reauthorization of the existing account."""
        return self.async_show_form(step_id="reauth_confirm")

    async def async_step_reauth_confirm(self, user_input: dict | None = None) -> dict:
        """Keep the configured application, falling back to native credential selection."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        entry = self._get_reauth_entry()
        return await self.async_step_pick_implementation(
            {"implementation": entry.data["auth_implementation"]}
        )

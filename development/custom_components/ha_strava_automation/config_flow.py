"""Independent consent, verified account identity and reauth via native HA flows."""

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.config_entry_oauth2_flow import AbstractOAuth2FlowHandler

from .auth import (
    REQUESTED_SCOPES,
    AccountVerificationError,
    async_fetch_athlete,
    granted_scopes,
    has_activity_access,
)
from .client import ActivityError
from .const import DOMAIN
from .migration import MigrationError, async_pause_source, async_prepare_migration
from .quota import async_get_budget


class StravaAutomationFlow(AbstractOAuth2FlowHandler, domain=DOMAIN):
    """OAuth tokens belong to this integration, independently of any companion."""

    DOMAIN = DOMAIN
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return StravaAutomationOptionsFlow()

    @property
    def logger(self) -> logging.Logger:
        """Return the integration logger."""
        return logging.getLogger(__name__)

    @property
    def extra_authorize_data(self) -> dict[str, str]:
        """Explain requested scopes in the setup UI; allow declined optional writes."""
        return {"scope": ",".join(REQUESTED_SCOPES), "approval_prompt": "force"}

    async def async_step_user(self, user_input=None):
        """Offer an explicit native connection handover when a source exists."""
        if self.hass.config_entries.async_entries("ha_strava"):
            return self.async_show_menu(step_id="user", menu_options=["new_account", "migrate"])
        return await self.async_step_new_account(user_input)

    async def async_step_new_account(self, user_input=None):
        return await super().async_step_user(user_input)

    async def async_step_migrate(self, user_input=None):
        """No account is paused, or new credentials stored, at the selection step."""
        sources = self.hass.config_entries.async_entries("ha_strava")
        errors = {}
        if user_input is not None:
            try:
                snapshot = await async_prepare_migration(self.hass, user_input["source_entry_id"])
            except MigrationError as err:
                errors["base"] = str(err)
            else:
                self._migration_expected = (
                    snapshot.source_entry_id,
                    snapshot.athlete_id,
                    snapshot.client_id,
                )
                return await self.async_step_migration_confirm()
        return self.async_show_form(
            step_id="migrate",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required("source_entry_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": entry.entry_id, "label": entry.title} for entry in sources
                            ]
                        )
                    )
                }
            ),
        )

    async def async_step_migration_confirm(self, user_input=None):
        """Refresh the preflight, then pause the old refresh owner before creation."""
        errors = {}
        if user_input is not None and user_input.get("confirm_pause") is True:
            try:
                snapshot = await async_prepare_migration(self.hass, self._migration_expected[0])
                if (
                    snapshot.source_entry_id,
                    snapshot.athlete_id,
                    snapshot.client_id,
                ) != self._migration_expected:
                    raise MigrationError("wrong_account")
                await self.async_set_unique_id(snapshot.athlete_id)
                self._abort_if_unique_id_configured()
                await async_pause_source(self.hass, snapshot)
            except MigrationError as err:
                errors["base"] = str(err)
            else:
                return self.async_create_entry(
                    title=f"Strava ({snapshot.athlete_id})",
                    data=snapshot.data,
                    options={"preview_only": True},
                )
        elif user_input is not None:
            errors["base"] = "migration_confirmation_required"
        return self.async_show_form(
            step_id="migration_confirm",
            errors=errors,
            data_schema=vol.Schema({vol.Required("confirm_pause", default=False): bool}),
        )

    async def async_oauth_create_entry(self, data: dict) -> dict:
        """Bind tokens only to the authenticated athlete, preserving reauth options."""
        # HA's native callback forwards state/code, not the scope query parameter.
        # Use the authenticated token exchange's documented scope response.
        scopes = granted_scopes(data["token"].get("scope"))
        if not has_activity_access(scopes):
            return self.async_abort(reason="missing_activity_scope")
        try:
            budget = await async_get_budget(self.hass, self.flow_impl.client_id)
            athlete_id = await async_fetch_athlete(self.hass, data["token"]["access_token"], budget)
        except AccountVerificationError as err:
            return self.async_abort(reason=str(err))
        except OSError, ActivityError, ValueError:
            return self.async_abort(reason="cannot_connect")
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


class StravaAutomationOptionsFlow(config_entries.OptionsFlow):
    """An account-wide lock takes effect immediately, including queued work."""

    async def async_step_init(self, user_input=None):
        options = self.config_entry.options
        selected = options.get("track_shoes", "disabled")
        errors = {}
        catalog_status = "Shoes loaded from this Strava account."
        shoes = []
        source = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        try:
            if source is None:
                raise ActivityError("source_not_loaded")
            shoes = await source.client.shoes()
        except ActivityError:
            catalog_status = "Shoe catalog unavailable. Reauthorize Strava to grant profile access."
        known = {shoe["id"] for shoe in shoes}
        if user_input is not None:
            requested = user_input.get("track_shoes", selected)
            if requested not in known and requested not in ("disabled", selected):
                errors["track_shoes"] = "invalid_shoes"
            else:
                saved = {k: v for k, v in user_input.items() if k != "reauthorize_strava"}
                if user_input.get("reauthorize_strava"):
                    source_entry = self.config_entry
                    if source_entry is not None:
                        source_entry.async_start_reauth(self.hass)
                return self.async_create_entry(data={**options, **saved, "track_shoes": requested})
        choices = [{"value": "disabled", "label": "Disabled"}] + [
            {"value": shoe["id"], "label": shoe["name"]} for shoe in shoes
        ]
        if selected != "disabled" and selected not in known:
            choices.append({"value": selected, "label": f"Unavailable shoe ({selected})"})
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "preview_only", default=self.config_entry.options.get("preview_only", True)
                    ): bool,
                    vol.Optional("track_shoes", default=selected): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=choices, mode=selector.SelectSelectorMode.DROPDOWN
                        )
                    ),
                    vol.Optional("reauthorize_strava", default=False): bool,
                }
            ),
            errors=errors,
            description_placeholders={"shoe_catalog_status": catalog_status},
        )

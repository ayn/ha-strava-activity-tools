"""Select an existing OAuth owner, without storing or duplicating credentials."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .client import ActivityClient, ActivityError
from .const import DOMAIN, SOURCE_DOMAIN
from .migration import LEGACY_DOMAIN, legacy_entry
from .session import HASession


class ActivityToolsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Companion integration prototype: the existing Strava entry owns OAuth."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ActivityToolsOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Only accept a loaded Strava entry and a numeric athlete ID."""
        sources = {
            entry.entry_id: entry
            for entry in self.hass.config_entries.async_entries(SOURCE_DOMAIN)
            if entry.entry_id in self.hass.data.get(SOURCE_DOMAIN, {})
        }
        if not sources:
            return self.async_abort(reason="source_not_loaded")
        legacy_options = [
            {"value": item.entry_id, "label": item.title}
            for item in self.hass.config_entries.async_entries(LEGACY_DOMAIN)
            if item.disabled_by is not None
        ]
        errors = {}
        if user_input is not None:
            source = sources.get(user_input["source_entry_id"])
            athlete_id = str(user_input["athlete_id"])
            if source is None:
                errors["base"] = "source_not_loaded"
            elif not athlete_id.isascii() or not athlete_id.isdecimal():
                errors["athlete_id"] = "invalid_athlete"
            elif source.unique_id is not None and source.unique_id != athlete_id:
                errors["athlete_id"] = "wrong_athlete"
            else:
                data = {"source_entry_id": source.entry_id, "athlete_id": athlete_id}
                options = {"preview_only": True}
                if legacy_id := user_input.get("legacy_entry_id"):
                    try:
                        old = legacy_entry(self.hass, legacy_id, data)
                    except ActivityError:
                        errors["legacy_entry_id"] = "legacy_unavailable"
                    else:
                        if "track_shoes" in old.options:
                            options["track_shoes"] = old.options["track_shoes"]
                        data["legacy_entry_id"] = legacy_id
                if errors:
                    return self._user_form(sources, legacy_options, errors)
                await self.async_set_unique_id(athlete_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Strava Activity Tools ({athlete_id})",
                    options=options,
                    data=data,
                )
        return self._user_form(sources, legacy_options, errors)

    def _user_form(self, sources, legacy_options, errors):
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("source_entry_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": entry.entry_id, "label": entry.title}
                                for entry in sources.values()
                            ]
                        )
                    ),
                    vol.Required("athlete_id"): str,
                    **(
                        {
                            vol.Optional("legacy_entry_id"): selector.SelectSelector(
                                selector.SelectSelectorConfig(options=legacy_options)
                            )
                        }
                        if legacy_options
                        else {}
                    ),
                }
            ),
            errors=errors,
        )


class ActivityToolsOptionsFlow(config_entries.OptionsFlow):
    """An account-wide lock takes effect immediately, including queued work."""

    async def async_step_init(self, user_input=None):
        options = self.config_entry.options
        selected = options.get("track_shoes", "disabled")
        errors = {}
        catalog_status = "Shoes loaded from this Strava account."
        shoes = []
        source = self.hass.data.get(SOURCE_DOMAIN, {}).get(
            self.config_entry.data["source_entry_id"]
        )
        try:
            if source is None:
                raise ActivityError("source_not_loaded")
            shoes = await ActivityClient(
                HASession(source.oauth_session), self.config_entry.data["athlete_id"]
            ).shoes()
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
                    source_entry = self.hass.config_entries.async_get_entry(
                        self.config_entry.data["source_entry_id"]
                    )
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

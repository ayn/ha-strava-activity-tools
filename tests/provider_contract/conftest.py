"""Opt-in tests load a real, independently checked-out provider package."""

import importlib
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from homeassistant.config_entries import ConfigEntryState
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components


@pytest.fixture(autouse=True)
def provider_package(monkeypatch, enable_custom_integrations):
    source = os.environ.get("HA_STRAVA_SOURCE")
    if not source:
        pytest.skip("Set HA_STRAVA_SOURCE to a real provider checkout for compatibility tests")
    path = Path(source).resolve() / "custom_components"
    if not (path / "ha_strava" / "manifest.json").is_file():
        pytest.fail("HA_STRAVA_SOURCE does not contain the provider integration")
    monkeypatch.setattr(custom_components, "__path__", [*custom_components.__path__, str(path)])
    provider = importlib.import_module("custom_components.ha_strava")
    assert Path(provider.__file__).resolve().is_relative_to(path), (
        "Wrong provider checkout imported"
    )
    return provider


@pytest.fixture
async def linked(hass, provider_package, socket_enabled, aiohttp_server, hass_client, monkeypatch):
    """Real HA entries, provider coordinator/OAuth session and inbound/outbound HTTP."""
    provider = provider_package
    constants = importlib.import_module("custom_components.ha_strava.const")
    assert getattr(constants, "EVENT_ACTIVITY_CREATED", None) == "ha_strava_activity_created", (
        "Missing required activity-created event (upstream #349); provider is not compatible"
    )
    activity = {
        "id": 9,
        "athlete": {"id": 42},
        "name": "Synthetic run",
        "sport_type": "Run",
        "distance": 1609.344,
        "hide_from_home": False,
        "trainer": False,
        "manual": False,
    }
    requests = []

    async def strava(request):
        requests.append((request.method, request.headers.get("Authorization")))
        if request.method == "PUT":
            assert dict(await request.post()) == {"hide_from_home": "true"}
            activity["hide_from_home"] = True
        return web.json_response(activity)

    app = web.Application()
    app.router.add_route("*", "/api/v3/activities/9", strava)
    server = await aiohttp_server(app)
    monkeypatch.setattr(
        "custom_components.strava_activity_tools.client.BASE_URL",
        str(server.make_url("/api/v3")),
    )
    entries = []
    for athlete in (42, 99):
        entry = MockConfigEntry(
            domain="ha_strava",
            unique_id=str(athlete),
            entry_id=f"source{athlete}",
            data={
                "client_id": "synthetic-client",
                "client_secret": "synthetic-secret",
                "webhook_id": 77,
                "token": {
                    "access_token": f"synthetic-{athlete}",
                    "refresh_token": "synthetic-refresh",
                    "expires_at": 4102444800,
                    "token_type": "Bearer",
                },
                "photos": False,
            },
        )
        entry.add_to_hass(hass)
        entries.append(entry)
    # Only external startup refresh/subscription registration is substituted. Real provider
    # setup, coordinator construction, OAuth, platform lifecycle and webhook view execute.
    with (
        patch.object(
            provider.StravaDataUpdateCoordinator,
            "_async_update_data",
            AsyncMock(
                return_value={"activities": [], "summary_stats": {}, "images": {}, "gear": []},
            ),
        ),
        patch.object(provider, "renew_webhook_subscription", AsyncMock()),
    ):
        assert await async_setup_component(hass, "ha_strava", {})
        assert all(entry.state is ConfigEntryState.LOADED for entry in entries)
        for athlete in (42, 99):
            entry = MockConfigEntry(
                domain="strava_activity_tools",
                entry_id=f"tools{athlete}",
                unique_id=str(athlete),
                data={"source_entry_id": f"source{athlete}", "athlete_id": str(athlete)},
                options={"preview_only": False},
            )
            entry.add_to_hass(hass)
        assert await async_setup_component(hass, "strava_activity_tools", {})
        await hass.async_block_till_done()
        assert all(
            entry.state is ConfigEntryState.LOADED
            for entry in hass.config_entries.async_entries("strava_activity_tools")
        )
        client = await hass_client()
        yield client, activity, requests, entries


def delivery(**changes):
    return {
        "owner_id": 42,
        "object_id": 9,
        "subscription_id": 77,
        "object_type": "activity",
        "aspect_type": "create",
        **changes,
    }

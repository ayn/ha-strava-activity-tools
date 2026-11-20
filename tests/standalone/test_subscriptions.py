"""Native HA webhook registration and a loopback Strava subscription API."""

from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from custom_components.ha_strava_automation.client import ActivityError
from custom_components.ha_strava_automation.quota import AppBudget
from custom_components.ha_strava_automation.subscriptions import SubscriptionManager
from homeassistant.components import webhook
from homeassistant.setup import async_setup_component

from .test_inbox import MemoryStore


@pytest.fixture
async def manager(hass, aiohttp_server, socket_enabled, monkeypatch):
    assert await async_setup_component(hass, "webhook", {})
    hass.config.external_url = "https://ha.example.test"
    remote, calls = [], []

    async def api(request):
        form = request.query if request.method in ("GET", "DELETE") else await request.post()
        assert form["client_id"] == "100"
        assert form["client_secret"] == "synthetic-client-secret"
        calls.append(request.method)
        if request.method == "GET":
            return web.json_response(remote)
        if request.method == "POST":
            remote.append({"id": 12, "callback_url": form["callback_url"]})
            return web.json_response({"id": 12}, status=201)
        remote.clear()
        return web.Response(status=204)

    app = web.Application()
    app.router.add_route("*", "/subscriptions", api)
    app.router.add_delete("/subscriptions/{id}", api)
    server = await aiohttp_server(app)
    monkeypatch.setattr(
        "custom_components.ha_strava_automation.subscriptions.SUBSCRIPTIONS_URL",
        str(server.make_url("/subscriptions")),
    )
    store = MemoryStore()
    quota = AppBudget(MemoryStore(), lambda: 1800000000)
    await quota.load()
    result = SubscriptionManager(hass, "100", "synthetic-client-secret", store, quota)
    await result.load()
    yield result, store, remote, calls
    for identifier in list(result.targets):
        await result.disconnect(identifier)


async def test_shared_app_registers_once_and_unload_does_not_delete(manager):
    manager, store, remote, calls = manager
    with patch.object(webhook, "async_register", wraps=webhook.async_register) as register:
        await manager.connect("42", AsyncMock())
        await manager.connect("99", AsyncMock())
        assert register.call_count == 1
    assert calls.count("POST") == 1
    assert len(remote) == 1
    assert store.data["subscription_id"] == "12"
    await manager.disconnect("42")
    assert "99" in manager.targets
    await manager.disconnect("99")
    assert "DELETE" not in calls


async def test_reload_reuses_the_private_callback_and_remote_subscription(manager):
    first, store, remote, calls = manager
    await first.connect("42", AsyncMock())
    callback = remote[0]["callback_url"]
    await first.disconnect("42")
    reloaded = SubscriptionManager(
        first.hass, "100", "synthetic-client-secret", store, first.budget
    )
    await reloaded.load()
    await reloaded.connect("42", AsyncMock())
    assert remote[0]["callback_url"] == callback
    assert calls.count("POST") == 1
    await reloaded.disconnect("42")


async def test_foreign_callback_conflict_is_never_taken_over(manager):
    manager, _, remote, calls = manager
    remote.append({"id": 99, "callback_url": "https://other.example.test/hook"})
    with pytest.raises(ActivityError, match="subscription_conflict"):
        await manager.connect("42", AsyncMock())
    assert calls == ["GET"]
    assert remote[0]["id"] == 99
    assert not manager.targets


async def test_explicit_cleanup_never_deletes_foreign_or_active_subscription(manager):
    manager, _, remote, calls = manager
    await manager.connect("42", AsyncMock())
    with pytest.raises(ActivityError, match="subscription_in_use"):
        await manager.remove()
    await manager.disconnect("42")
    remote[0]["callback_url"] = "https://other.example.test/hook"
    with pytest.raises(ActivityError, match="subscription_conflict"):
        await manager.remove()
    assert "DELETE" not in calls


async def test_explicit_last_owner_cleanup_checks_remote_ownership(manager):
    manager, store, remote, calls = manager
    await manager.connect("42", AsyncMock())
    await manager.disconnect("42")
    await manager.remove()
    assert not remote
    assert calls[-2:] == ["GET", "DELETE"]
    assert store.data["subscription_id"] is None


async def test_explicit_handover_replaces_only_the_exact_verified_old_callback(manager):
    manager, _, remote, calls = manager
    old = {"id": "99", "callback_url": "https://old.example.test/api/strava/webhook"}
    remote.append({"id": 99, "callback_url": old["callback_url"]})
    await manager.connect("42", AsyncMock(), takeover=old)
    assert calls == ["GET", "DELETE", "POST"]
    assert remote[0]["callback_url"] == manager.record["callback_url"]


async def test_handover_rejects_changed_remote_id_without_deleting_it(manager):
    manager, _, remote, calls = manager
    old = {"id": "98", "callback_url": "https://old.example.test/api/strava/webhook"}
    remote.append({"id": 99, "callback_url": old["callback_url"]})
    with pytest.raises(ActivityError, match="subscription_conflict"):
        await manager.connect("42", AsyncMock(), takeover=old)
    assert calls == ["GET"]
    assert remote[0]["id"] == 99

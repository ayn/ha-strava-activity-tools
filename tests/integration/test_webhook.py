"""Actual HA webhook router with synthetic requests and a durable-accept boundary."""

from unittest.mock import AsyncMock

import pytest
from homeassistant.components import webhook
from homeassistant.setup import async_setup_component

from custom_components.ha_strava_automation.webhook import WebhookReceiver

from .conftest import DOMAIN

NOW = 1800000000
PATH = "/api/webhook/synthetic-private-webhook-id"


@pytest.fixture
async def receiver(hass, hass_client_no_auth):
    assert await async_setup_component(hass, "webhook", {})
    accept = AsyncMock()
    receiver = WebhookReceiver(
        subscription_id="12",
        verify_token="synthetic-verification-token",
        targets={"42": accept},
        now=lambda: NOW,
    )
    webhook.async_register(
        hass,
        DOMAIN,
        "Synthetic app",
        PATH.rsplit("/", 1)[1],
        receiver.handle,
        allowed_methods=("GET", "POST"),
    )
    client = await hass_client_no_auth()
    yield client, receiver, accept
    webhook.async_unregister(hass, PATH.rsplit("/", 1)[1])


def event(**updates):
    return {
        "subscription_id": 12,
        "owner_id": 42,
        "object_id": 123,
        "object_type": "activity",
        "aspect_type": "create",
        "event_time": NOW,
        **updates,
    }


async def test_challenge_verifies_token_before_echoing(receiver):
    client, _, accept = receiver
    result = await client.get(
        PATH,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "synthetic-verification-token",
            "hub.challenge": "test-challenge",
        },
    )
    assert result.status == 200
    assert await result.json() == {"hub.challenge": "test-challenge"}
    accept.assert_not_called()


async def test_bad_challenge_does_not_echo(receiver):
    client, _, accept = receiver
    result = await client.get(
        PATH,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong",
            "hub.challenge": "do-not-echo",
        },
    )
    assert result.status == 400
    assert "do-not-echo" not in await result.text()
    accept.assert_not_called()


async def test_create_is_durably_accepted_before_ack(receiver):
    client, _, accept = receiver
    result = await client.post(PATH, json=event())
    assert result.status == 200
    accept.assert_awaited_once_with("123")


@pytest.mark.parametrize("payload", [[], None, True, "activity", {"owner_id": 42}])
async def test_malformed_payload_never_enqueues(receiver, payload):
    client, _, accept = receiver
    result = await client.post(PATH, json=payload)
    assert result.status == 400
    accept.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("object_id", True),
        ("object_id", "１２３"),
        ("object_id", -1),
        ("object_id", 2**63),
        ("owner_id", False),
        ("event_time", True),
        ("subscription_id", "012"),
    ],
)
async def test_bad_scalar_fields_never_enqueues(receiver, field, value):
    client, _, accept = receiver
    result = await client.post(PATH, json=event(**{field: value}))
    assert result.status == 400
    accept.assert_not_called()


@pytest.mark.parametrize(
    "updates",
    [
        {"subscription_id": 99},
        {"owner_id": 99},
        {"object_type": "athlete"},
        {"aspect_type": "update"},
        {"aspect_type": "delete"},
        {"event_time": NOW + 3600},
        {"event_time": NOW - 8 * 86400},
    ],
)
async def test_irrelevant_and_stale_events_do_not_process(receiver, updates):
    client, _, accept = receiver
    result = await client.post(PATH, json=event(**updates))
    assert result.status == 200
    accept.assert_not_called()


async def test_disabled_or_unloaded_target_is_not_routed(receiver):
    client, receiver, accept = receiver
    receiver.targets.clear()
    result = await client.post(PATH, json=event())
    assert result.status == 200
    accept.assert_not_called()


async def test_storage_failure_does_not_ack_lost_delivery(receiver):
    client, _, accept = receiver
    accept.side_effect = OSError("private storage details")
    result = await client.post(PATH, json=event())
    assert result.status == 503
    assert "private storage details" not in await result.text()


async def test_oversized_body_is_rejected_before_queue(receiver):
    client, _, accept = receiver
    result = await client.post(PATH, json=event(extra="x" * 20000))
    assert result.status == 413
    accept.assert_not_called()


async def test_registration_window_requests_redelivery_instead_of_losing_event(receiver):
    client, receiver, accept = receiver
    receiver.subscription_id = None
    result = await client.post(PATH, json=event())
    assert result.status == 503
    accept.assert_not_called()


async def test_unicode_challenge_token_is_rejected_without_server_error(receiver):
    client, _, accept = receiver
    result = await client.get(
        PATH,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "错误",
            "hub.challenge": "do-not-echo",
        },
    )
    assert result.status == 400
    accept.assert_not_called()

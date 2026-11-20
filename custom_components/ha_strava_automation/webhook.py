"""Independent webhook ingress, developed against Strava's public protocol."""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from secrets import compare_digest

from aiohttp import web

MAX_BODY = 16384


class DeliveryUnavailable(Exception):
    """The durable queue could not accept this delivery safely."""


def strava_id(value: object) -> str | None:
    """Canonical positive ASCII IDs; booleans, floats and padding are invalid."""
    if type(value) is int:
        return str(value) if 0 < value < 2**63 else None
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,18}", value):
        return value if int(value) < 2**63 else None
    return None


class WebhookReceiver:
    """Unsigned delivery only schedules owner-checked work; it grants no authority."""

    def __init__(
        self,
        *,
        subscription_id: str,
        verify_token: str,
        targets: dict[str, Callable[[str], Awaitable[None]]],
        now: Callable[[], float],
    ):
        self.subscription_id = subscription_id
        self.verify_token = verify_token
        self.targets = targets
        self.now = now

    async def handle(self, hass, webhook_id, request):
        """Validate a bounded request and persist an intent before acknowledgement."""
        if request.method == "GET":
            query = request.query
            challenge = query.get("hub.challenge", "")
            if (
                query.get("hub.mode") != "subscribe"
                or not compare_digest(
                    query.get("hub.verify_token", "").encode(), self.verify_token.encode()
                )
                or not challenge
                or len(challenge) > 256
            ):
                return web.json_response({}, status=400)
            return web.json_response({"hub.challenge": challenge})
        if request.content_length is not None and request.content_length > MAX_BODY:
            return web.json_response({}, status=413)
        raw = bytearray()
        async for chunk in request.content.iter_chunked(4096):
            raw.extend(chunk)
            if len(raw) > MAX_BODY:
                return web.json_response({}, status=413)
        try:
            body = json.loads(raw)
        except ValueError, UnicodeDecodeError:
            return web.json_response({}, status=400)
        if not isinstance(body, dict):
            return web.json_response({}, status=400)
        values = [
            strava_id(body.get(key))
            for key in ("subscription_id", "owner_id", "object_id", "event_time")
        ]
        if None in values:
            return web.json_response({}, status=400)
        subscription, owner, identifier, stamp = values
        if self.subscription_id is None and owner in self.targets:
            return web.json_response({}, status=503)
        if (
            subscription != self.subscription_id
            or owner not in self.targets
            or body.get("object_type") != "activity"
            or body.get("aspect_type") != "create"
            or not self.now() - 7 * 86400 <= int(stamp) <= self.now() + 300
        ):
            return web.json_response({})
        try:
            async with asyncio.timeout(1.5):
                await self.targets[owner](identifier)
        except OSError, TimeoutError, DeliveryUnavailable:
            return web.json_response({}, status=503)
        return web.json_response({})

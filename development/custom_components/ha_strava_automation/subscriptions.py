"""One remotely owned subscription per API app, shared by local accounts."""

import asyncio
import re
import secrets

from aiohttp import ClientError
from homeassistant.components import webhook
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from yarl import URL

from .client import ActivityError
from .const import DOMAIN
from .quota import async_get_budget
from .webhook import WebhookReceiver, strava_id

SUBSCRIPTIONS_URL = "https://www.strava.com/api/v3/push_subscriptions"


class SubscriptionManager:
    def __init__(self, hass, client_id, client_secret, store, budget):
        self.hass, self.client_id, self.client_secret = hass, client_id, client_secret
        self.store, self.budget = store, budget
        self.targets = {}
        self.record = {}
        self.receiver = None
        self.registered = False
        self.lock = asyncio.Lock()

    async def load(self):
        record = await self.store.async_load()
        if record is None:
            record = {
                "webhook_id": webhook.async_generate_id(),
                "verify_token": secrets.token_urlsafe(32),
                "subscription_id": None,
                "callback_url": None,
            }
            await self.store.async_save(record)
        if (
            not isinstance(record, dict)
            or set(record) != {"webhook_id", "verify_token", "subscription_id", "callback_url"}
            or any(
                not isinstance(record[k], str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", record[k])
                for k in ("webhook_id", "verify_token")
            )
            or (
                record["subscription_id"] is not None
                and strava_id(record["subscription_id"]) != record["subscription_id"]
            )
        ):
            raise ActivityError("invalid_subscription_store")
        if record["callback_url"] is not None:
            self._check_url(record["callback_url"])
        self.record = record
        self.receiver = WebhookReceiver(
            subscription_id=record["subscription_id"],
            verify_token=record["verify_token"],
            targets=self.targets,
            now=lambda: dt_util.utcnow().timestamp(),
        )

    @staticmethod
    def _check_url(value):
        if not isinstance(value, str) or len(value) > 255:
            raise ActivityError("invalid_callback_url")
        url = URL(value)
        if url.scheme != "https" or not url.host or url.user or url.password or url.fragment:
            raise ActivityError("invalid_callback_url")

    async def _callback_url(self):
        if self.record["callback_url"] is not None:
            return self.record["callback_url"]
        try:
            return webhook.async_generate_url(
                self.hass, self.record["webhook_id"], allow_internal=False
            )
        except NoURLAvailableError:
            pass
        if "cloud" in self.hass.config.components:
            from homeassistant.components import cloud

            if cloud.async_is_logged_in(self.hass):
                return await cloud.async_get_or_create_cloudhook(
                    self.hass, self.record["webhook_id"]
                )
        raise NoURLAvailableError

    async def request(self, method, identifier=None, **fields):
        """Never expose request URLs/errors: subscription queries contain app secrets."""
        url = SUBSCRIPTIONS_URL + (f"/{identifier}" if identifier else "")
        values = {"client_id": self.client_id, "client_secret": self.client_secret, **fields}
        try:
            await self.budget.reserve()
            async with asyncio.timeout(20):
                async with async_get_clientsession(self.hass).request(
                    method,
                    url,
                    allow_redirects=False,
                    **({"params": values} if method in ("GET", "DELETE") else {"data": values}),
                ) as response:
                    await self.budget.observe(response.headers, response.status)
                    if response.status == 429:
                        raise ActivityError("rate_limited", 900)
                    if response.status in (401, 403):
                        raise ActivityError("reauth_required")
                    if response.status not in (200, 201, 204):
                        raise ActivityError("upstream_unavailable")
                    return None if response.status == 204 else await response.json()
        except ActivityError:
            raise
        except ClientError, TimeoutError, ValueError:
            raise ActivityError("upstream_unavailable") from None
        except OSError:
            raise ActivityError("job_storage_unavailable") from None

    async def _ensure_remote(self, takeover=None):
        records = await self.request("GET")
        if not isinstance(records, list) or len(records) > 1:
            raise ActivityError("invalid_subscription_response")
        if (
            records
            and isinstance(records[0], dict)
            and records[0].get("callback_url") != self.record["callback_url"]
        ):
            item = records[0]
            if (
                not isinstance(takeover, dict)
                or len(self.targets) != 1
                or strava_id(item.get("id")) != takeover.get("id")
                or item.get("callback_url") != takeover.get("callback_url")
            ):
                raise ActivityError("subscription_conflict")
            await self.request("DELETE", identifier=takeover["id"])
            records = []
        if records:
            item = records[0]
            if (
                not isinstance(item, dict)
                or strava_id(item.get("id")) is None
                or item.get("callback_url") != self.record["callback_url"]
                or (
                    self.record["subscription_id"] is not None
                    and strava_id(item["id"]) != self.record["subscription_id"]
                )
            ):
                raise ActivityError("subscription_conflict")
            identifier = strava_id(item["id"])
        else:
            self.receiver.subscription_id = None
            item = await self.request(
                "POST",
                callback_url=self.record["callback_url"],
                verify_token=self.record["verify_token"],
            )
            identifier = strava_id(item.get("id")) if isinstance(item, dict) else None
            if identifier is None:
                raise ActivityError("invalid_subscription_response")
        record = {**self.record, "subscription_id": identifier}
        await self.store.async_save(record)
        self.record = record
        self.receiver.subscription_id = identifier

    async def connect(self, athlete_id, accept, *, takeover=None):
        async with self.lock:
            if athlete_id in self.targets:
                raise ActivityError("duplicate_account")
            callback_url = await self._callback_url()
            self._check_url(callback_url)
            record = {**self.record, "callback_url": callback_url}
            await self.store.async_save(record)
            self.record = record
            self.targets[athlete_id] = accept
            if not self.registered:
                webhook.async_register(
                    self.hass,
                    DOMAIN,
                    "Strava Automation",
                    record["webhook_id"],
                    self.receiver.handle,
                    local_only=False,
                    allowed_methods=("GET", "POST"),
                )
                self.registered = True
            try:
                await self._ensure_remote(takeover)
            except Exception:
                self._disconnect(athlete_id)
                raise

    def _disconnect(self, athlete_id):
        self.targets.pop(athlete_id, None)
        if not self.targets and self.registered:
            webhook.async_unregister(self.hass, self.record["webhook_id"])
            self.registered = False

    async def disconnect(self, athlete_id):
        async with self.lock:
            self._disconnect(athlete_id)

    async def remove(self):
        async with self.lock:
            if self.targets:
                raise ActivityError("subscription_in_use")
            records = await self.request("GET")
            if not isinstance(records, list) or len(records) > 1:
                raise ActivityError("invalid_subscription_response")
            if records:
                item = records[0]
                if (
                    not isinstance(item, dict)
                    or strava_id(item.get("id")) is None
                    or item.get("callback_url") != self.record["callback_url"]
                    or strava_id(item.get("id")) != self.record["subscription_id"]
                ):
                    raise ActivityError("subscription_conflict")
                await self.request("DELETE", identifier=self.record["subscription_id"])
            record = {**self.record, "subscription_id": None}
            await self.store.async_save(record)
            self.record = record


async def async_get_application(hass, entry, session):
    """Share one budget, local route and registration lock per app across entries."""
    client_id = entry.data["client_id"]
    if strava_id(client_id) != client_id:
        raise ActivityError("unverified_application")
    apps = hass.data.setdefault(f"{DOMAIN}_applications", {})
    locks = hass.data.setdefault(f"{DOMAIN}_application_locks", {})
    lock = locks.setdefault(client_id, asyncio.Lock())
    async with lock:
        if client_id not in apps:
            budget = await async_get_budget(hass, client_id)
            manager = SubscriptionManager(
                hass,
                client_id,
                session.implementation.client_secret,
                Store(
                    hass,
                    1,
                    f"{DOMAIN}.app.{client_id}.subscription",
                    private=True,
                    atomic_writes=True,
                ),
                budget,
            )
            await manager.load()
            apps[client_id] = manager
        else:
            apps[client_id].client_secret = session.implementation.client_secret
        return apps[client_id]

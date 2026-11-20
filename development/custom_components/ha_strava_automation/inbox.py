"""Bounded durable acceptance independent of the API worker's long-held lock."""

import asyncio
import math
from copy import deepcopy

from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .webhook import DeliveryUnavailable, strava_id

CAPACITY = 512
TTL = 7 * 86400


class DurableInbox:
    """Persist webhook acceptance without waiting for API side effects."""

    def __init__(self, store, owner, clock):
        self.store, self.owner, self.clock = store, owner, clock
        self.ids = {}
        self._lock = asyncio.Lock()
        self._drain_lock = asyncio.Lock()

    async def load(self):
        """Refuse corruption or a different owner instead of losing pending work."""
        async with self._lock:
            data = await self.store.async_load()
            if data is None:
                return
            if (
                not isinstance(data, dict)
                or data.get("owner") != self.owner
                or not isinstance(data.get("pending"), dict)
                or len(data["pending"]) > CAPACITY
            ):
                raise ValueError("invalid_inbox")
            restored = {}
            for identifier, stamp in data["pending"].items():
                if (
                    strava_id(identifier) != identifier
                    or type(stamp) not in (int, float)
                    or not math.isfinite(stamp)
                    or stamp < 0
                    or stamp > self.clock() + 300
                ):
                    raise ValueError("invalid_inbox")
                if self.clock() - stamp < TTL:
                    restored[identifier] = stamp
            await self._save(restored)

    async def _save(self, values):
        await self.store.async_save({"owner": self.owner, "pending": deepcopy(values)})
        self.ids = values

    async def accept(self, activity_id):
        """Only the atomic disk write, never API work, holds the acceptance lock."""
        if strava_id(activity_id) != activity_id:
            raise ValueError("invalid_activity_id")
        async with self._lock:
            values = {k: v for k, v in self.ids.items() if self.clock() - v < TTL}
            if activity_id in values:
                return
            if len(values) >= CAPACITY:
                raise DeliveryUnavailable("inbox_full")
            values[activity_id] = self.clock()
            await self._save(values)

    async def drain(self, enqueue):
        """Drop an inbox ID only after the destination has persisted its intent."""
        async with self._drain_lock:
            for identifier in list(self.ids):
                await enqueue(identifier)
                async with self._lock:
                    values = dict(self.ids)
                    values.pop(identifier, None)
                    await self._save(values)


class InboxPump:
    """Bridge durable acceptance to the account worker without blocking HTTP."""

    def __init__(self, hass, entry, worker):
        self.hass, self.entry, self.worker = hass, entry, worker
        self.inbox = DurableInbox(
            Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.inbox", private=True, atomic_writes=True),
            {"client_id": entry.data["client_id"], "athlete_id": entry.data["athlete_id"]},
            lambda: dt_util.utcnow().timestamp(),
        )
        self.task = None
        self.cancel_retry = None
        self.closed = False

    async def load(self):
        await self.inbox.load()
        self.arm()

    async def accept(self, activity_id):
        if self.closed:
            raise DeliveryUnavailable("account_unloaded")
        await self.inbox.accept(activity_id)
        self.arm()

    def arm(self):
        if (
            not self.closed
            and self.cancel_retry is None
            and self.inbox.ids
            and (self.task is None or self.task.done())
        ):
            self.task = self.entry.async_create_background_task(
                self.hass, self._run(), f"{DOMAIN}_inbox"
            )

    async def _run(self):
        async def enqueue(identifier):
            await self.worker.queue.submit("fetch", identifier)
            self.worker.arm()

        try:
            while not self.closed and self.inbox.ids:
                await self.inbox.drain(enqueue)
        except Exception:
            # Queue/storage recovery is retried separately; HTTP already persisted.
            # Keep all IDs, never log raw storage exceptions or activity bodies.
            if not self.closed:

                def retry(_now):
                    self.cancel_retry = None
                    self.arm()

                self.cancel_retry = async_call_later(self.hass, 60, retry)

    async def close(self):
        self.closed = True
        if self.cancel_retry:
            self.cancel_retry()
        if self.task is not None and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

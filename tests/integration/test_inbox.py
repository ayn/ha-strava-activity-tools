"""Durable webhook acceptance must never wait for network work under its lock."""

import asyncio
from copy import deepcopy

import pytest

from custom_components.ha_strava_automation.inbox import DurableInbox


class MemoryStore:
    def __init__(self, data=None):
        self.data = deepcopy(data)
        self.fail = False

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        if self.fail:
            raise OSError("synthetic storage failure")
        self.data = deepcopy(data)


def inbox(store):
    return DurableInbox(store, {"athlete_id": "42", "client_id": "100"}, lambda: 1800000000)


async def test_accept_survives_restart_and_duplicates_are_bounded():
    store = MemoryStore()
    first = inbox(store)
    await first.load()
    await first.accept("123")
    await first.accept("123")
    restarted = inbox(store)
    await restarted.load()
    delivered = []

    async def enqueue(identifier):
        delivered.append(identifier)

    await restarted.drain(enqueue)
    assert delivered == ["123"]
    assert store.data["pending"] == {}


async def test_api_worker_block_does_not_block_new_durable_acceptance():
    store = MemoryStore()
    queue = inbox(store)
    await queue.load()
    await queue.accept("123")
    started, release = asyncio.Event(), asyncio.Event()

    async def blocked(_identifier):
        started.set()
        await release.wait()

    task = asyncio.create_task(queue.drain(blocked))
    try:
        async with asyncio.timeout(1):
            await started.wait()
            await queue.accept("124")
        assert "124" in store.data["pending"]
    finally:
        release.set()
        await task


async def test_failed_destination_enqueue_preserves_inbox():
    store = MemoryStore()
    queue = inbox(store)
    await queue.accept("123")

    async def fail(_identifier):
        raise OSError("destination not saved")

    with pytest.raises(OSError):
        await queue.drain(fail)
    assert "123" in store.data["pending"]


async def test_failed_accept_never_commits_in_memory():
    store = MemoryStore()
    queue = inbox(store)
    store.fail = True
    with pytest.raises(OSError):
        await queue.accept("123")
    assert queue.ids == {}


async def test_owner_mismatch_does_not_reset_or_replay():
    store = MemoryStore(
        {"owner": {"athlete_id": "99", "client_id": "100"}, "pending": {"123": 1800000000}}
    )
    old = deepcopy(store.data)
    with pytest.raises(ValueError):
        await inbox(store).load()
    assert store.data == old

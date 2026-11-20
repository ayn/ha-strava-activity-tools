"""Real response quotas must gate every account sharing an API app."""

import pytest
from custom_components.ha_strava_automation.client import ActivityError
from custom_components.ha_strava_automation.quota import AppBudget

from .test_inbox import MemoryStore


async def budget():
    now = [1800000000.0]
    store = MemoryStore()
    result = AppBudget(store, lambda: now[0], short_reserve=1, daily_reserve=1)
    await result.load()
    return result, store, now


async def test_observed_read_limit_protects_reserve_even_if_overall_has_room():
    quota, _, _ = await budget()
    await quota.observe(
        {
            "X-RateLimit-Limit": "400,4000",
            "X-RateLimit-Usage": "20,20",
            "X-ReadRateLimit-Limit": "200,2000",
            "X-ReadRateLimit-Usage": "199,199",
        }
    )
    with pytest.raises(ActivityError, match="rate_limited"):
        await quota.reserve()


async def test_budget_is_persisted_before_request_and_survives_reload():
    quota, store, now = await budget()
    await quota.observe({"X-ReadRateLimit-Limit": "3,100", "X-ReadRateLimit-Usage": "1,1"})
    await quota.reserve()
    reloaded = AppBudget(store, lambda: now[0], short_reserve=1, daily_reserve=1)
    await reloaded.load()
    with pytest.raises(ActivityError, match="rate_limited"):
        await reloaded.reserve()


async def test_quarter_hour_reset_does_not_reset_daily_usage():
    quota, _, now = await budget()
    await quota.observe({"X-ReadRateLimit-Limit": "3,4", "X-ReadRateLimit-Usage": "2,3"})
    now[0] += 900
    with pytest.raises(ActivityError, match="rate_limited") as caught:
        await quota.reserve()
    assert caught.value.retry_after > 900


async def test_midnight_utc_reset_restores_budget():
    quota, _, now = await budget()
    await quota.observe({"X-ReadRateLimit-Limit": "3,4", "X-ReadRateLimit-Usage": "2,3"})
    now[0] = (int(now[0] // 86400) + 1) * 86400
    await quota.reserve()


async def test_429_blocks_all_accounts_until_retry_deadline():
    quota, store, now = await budget()
    await quota.observe({"Retry-After": "120"}, status=429)
    another = AppBudget(store, lambda: now[0], short_reserve=1, daily_reserve=1)
    await another.load()
    with pytest.raises(ActivityError, match="rate_limited") as caught:
        await another.reserve()
    assert caught.value.retry_after >= 120


async def test_storage_failure_prevents_sending_a_request():
    quota, store, _ = await budget()
    store.fail = True
    with pytest.raises(OSError):
        await quota.reserve()

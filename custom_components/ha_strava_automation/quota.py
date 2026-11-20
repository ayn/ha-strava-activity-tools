"""App-scoped API budgets, separate from account-local operation receipts."""

import asyncio
import math
import re
from copy import deepcopy

from .client import ActivityError
from .const import DOMAIN


class AppBudget:
    def __init__(self, store, clock, short_reserve=5, daily_reserve=25):
        self.store, self.clock = store, clock
        self.short_reserve, self.daily_reserve = short_reserve, daily_reserve
        self.lock = asyncio.Lock()
        self.data = {
            "window": int(clock() // 900),
            "day": int(clock() // 86400),
            "limits": [200, 2000, 100, 1000],
            "used": [0, 0, 0, 0],
            "blocked_until": 0,
        }

    async def load(self):
        data = await self.store.async_load()
        if data is None:
            return
        if (
            not isinstance(data, dict)
            or set(data) != set(self.data)
            or any(type(data[k]) is not int or data[k] < 0 for k in ("window", "day"))
            or type(data["blocked_until"]) not in (int, float)
            or not math.isfinite(data["blocked_until"])
            or data["blocked_until"] < 0
        ):
            raise ValueError("invalid_quota_store")
        for key in ("limits", "used"):
            if (
                not isinstance(data[key], list)
                or len(data[key]) != 4
                or any(
                    type(x) is not int or x < (1 if key == "limits" else 0) or x > 2**31
                    for x in data[key]
                )
            ):
                raise ValueError("invalid_quota_store")
        self.data = deepcopy(data)

    def _current(self):
        data = deepcopy(self.data)
        now = self.clock()
        window, day = int(now // 900), int(now // 86400)
        if window != data["window"]:
            data["used"][0] = data["used"][2] = 0
        if day != data["day"]:
            data["used"][1] = data["used"][3] = 0
        data.update(window=window, day=day)
        return data

    async def _save(self, data):
        await self.store.async_save(deepcopy(data))
        self.data = data

    async def reserve(self):
        async with self.lock:
            data = self._current()
            now = self.clock()
            deadlines = [data["blocked_until"]]
            for i, (used, limit) in enumerate(zip(data["used"], data["limits"], strict=True)):
                reserve = min(limit - 1, self.short_reserve if i % 2 == 0 else self.daily_reserve)
                if used >= limit - reserve:
                    period = 900 if i % 2 == 0 else 86400
                    deadlines.append((int(now // period) + 1) * period)
            until = max(deadlines)
            if until > now:
                raise ActivityError("rate_limited", max(1, math.ceil(until - now)))
            # These are non-upload calls: Strava counts GET and PUT against both
            # budgets. Reserve before HTTP so concurrent accounts cannot overspend.
            data["used"] = [x + 1 for x in data["used"]]
            await self._save(data)

    async def observe(self, headers, status=200):
        async with self.lock:
            data = self._current()
            fields = {str(k).lower(): str(v) for k, v in headers.items()}
            for header, key, indexes in (
                ("x-ratelimit-limit", "limits", (0, 1)),
                ("x-ratelimit-usage", "used", (0, 1)),
                ("x-readratelimit-limit", "limits", (2, 3)),
                ("x-readratelimit-usage", "used", (2, 3)),
            ):
                text = fields.get(header, "")
                if not re.fullmatch(r"[0-9]{1,10},\s*[0-9]{1,10}", text):
                    continue
                values = [int(v.strip()) for v in text.split(",")]
                if any(v > 2**31 or v < (1 if key == "limits" else 0) for v in values):
                    continue
                for i, value in zip(indexes, values, strict=True):
                    data[key][i] = max(data[key][i], value) if key == "used" else value
            if status == 429:
                text = fields.get("retry-after", "")
                delay = min(7 * 86400, int(text)) if re.fullmatch(r"[0-9]{1,10}", text) else 900
                data["blocked_until"] = max(data["blocked_until"], self.clock() + max(1, delay))
            await self._save(data)


async def async_get_budget(hass, client_id):
    """One budget object per app, also used during consent/account verification."""
    from homeassistant.helpers.storage import Store
    from homeassistant.util import dt as dt_util

    from .webhook import strava_id

    if strava_id(client_id) != client_id:
        raise ActivityError("unverified_application")
    budgets = hass.data.setdefault(f"{DOMAIN}_budgets", {})
    locks = hass.data.setdefault(f"{DOMAIN}_budget_locks", {})
    async with locks.setdefault(client_id, asyncio.Lock()):
        if client_id not in budgets:
            budget = AppBudget(
                Store(hass, 1, f"{DOMAIN}.app.{client_id}.quota", private=True, atomic_writes=True),
                lambda: dt_util.utcnow().timestamp(),
            )
            await budget.load()
            budgets[client_id] = budget
        return budgets[client_id]

"""Owner-checked operations using the existing integration's OAuth session."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any, Protocol

from aiohttp import ClientError

BASE_URL = "https://www.strava.com/api/v3"


class OAuthSession(Protocol):
    """The owner refreshes its own credentials; this client stores no tokens."""

    async def async_request(self, method: str, url: str, **kwargs: Any) -> Any:
        """Send an authenticated request."""


class ActivityError(Exception):
    """Stable, non-sensitive error category for HA traces."""

    def __init__(self, category: str, retry_after: int = 0) -> None:
        super().__init__(category)
        self.category = category
        self.retry_after = retry_after


def field_matches(key: str, actual: Any, expected: Any) -> bool:
    """Clearing gear sends 'none', but the API returns null for no gear."""
    return actual == (None if key == "gear_id" and expected == "none" else expected)


def validate_changes(changes: dict[str, Any]) -> None:
    """Allowlisted fields, including the explicit workout_type compatibility exception."""
    if not changes or not isinstance(changes, dict):
        raise ActivityError("invalid_changes")
    if "workout_type" in changes and "sport_type" in changes:
        raise ActivityError("invalid_changes")
    for key, value in changes.items():
        if key in ("hide_from_home", "trainer", "commute"):
            valid = type(value) is bool
        elif key in ("name", "description", "sport_type"):
            valid = isinstance(value, str)
        elif key == "gear_id":
            valid = isinstance(value, str) and re.fullmatch(r"(?:[bg]\d+|none)", value)
        elif key == "workout_type":
            # Compatibility field used by existing clients but omitted from current Swagger.
            valid = type(value) is int and value in (0, 1, 2, 3, 10, 11, 12)
        else:
            valid = False
        if not valid:
            raise ActivityError("invalid_changes")


def validate_expected(expected: dict[str, Any] | None) -> None:
    """Preconditions only accept the classification fields returned by previews."""
    if expected is None:
        return
    if not isinstance(expected, dict) or not expected:
        raise ActivityError("invalid_preconditions")
    for key, value in expected.items():
        if key in ("trainer", "manual"):
            valid = value is None or type(value) is bool
        elif key == "workout_type":
            valid = value is None or (type(value) is int and value in (0, 1, 2, 3, 10, 11, 12))
        elif key == "gear_id":
            valid = value is None or (isinstance(value, str) and re.fullmatch(r"[bg][0-9]+", value))
        elif key == "sport_type":
            valid = isinstance(value, str) and bool(value)
        else:
            valid = False
        if not valid:
            raise ActivityError("invalid_preconditions")


class ActivityClient:
    """Serialize modifications per athlete and re-read before touching a field."""

    def __init__(self, session: OAuthSession, athlete_id: str) -> None:
        if isinstance(athlete_id, bool) or not re.fullmatch(r"[1-9][0-9]*", str(athlete_id)):
            raise ActivityError("unverified_account")
        self.session = session
        self.athlete_id = str(athlete_id)
        self._lock = asyncio.Lock()

    async def _json_request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self.session.async_request(
                method,
                f"{BASE_URL}/{path}",
                timeout=20,
                **kwargs,
            )
            try:
                status = response.status
                if status != 200:
                    category = {
                        400: "invalid_request",
                        422: "invalid_request",
                        401: "reauth_required",
                        403: "write_scope_or_access_denied",
                        404: "activity_not_found",
                        429: "rate_limited",
                    }.get(status, "upstream_unavailable")
                    try:
                        retry_after = min(
                            7 * 86400, max(0, int(response.headers.get("Retry-After", "900")))
                        )
                    except TypeError, ValueError:
                        retry_after = 900
                    raise ActivityError(category, retry_after if status == 429 else 0)
                result = await response.json()
            finally:
                response.release()
        except ActivityError:
            raise
        except ClientError, TimeoutError, ValueError:
            raise ActivityError("upstream_unavailable") from None
        if not isinstance(result, dict):
            raise ActivityError("invalid_activity_response")
        return result

    async def _request(self, method: str, activity_id: str, **kwargs: Any) -> dict[str, Any]:
        activity_id = str(activity_id)
        if not re.fullmatch(r"[0-9]+", activity_id):
            raise ActivityError("invalid_activity_id")
        result = await self._json_request(method, f"activities/{activity_id}", **kwargs)
        if str(result.get("id")) != activity_id:
            raise ActivityError("invalid_activity_response")
        if not isinstance(result.get("athlete"), dict):
            raise ActivityError("invalid_activity_response")
        if str(result["athlete"].get("id")) != self.athlete_id:
            raise ActivityError("wrong_athlete")
        return result

    async def shoes(self) -> list[dict[str, Any]]:
        """Load the selected athlete's active shoes on demand, never by polling."""
        result = await self._json_request("GET", "athlete")
        if str(result.get("id")) != self.athlete_id:
            raise ActivityError("wrong_athlete")
        shoes = result.get("shoes")
        if not isinstance(shoes, list):
            raise ActivityError("shoe_catalog_unavailable")
        return [
            {
                "id": shoe["id"],
                "name": str(shoe.get("name") or shoe["id"]),
                "primary": shoe.get("primary") is True,
            }
            for shoe in shoes
            if isinstance(shoe, dict)
            and isinstance(shoe.get("id"), str)
            and re.fullmatch(r"g[0-9]+", shoe["id"])
            and not shoe.get("retired")
        ]

    async def get(self, activity_id: str) -> dict[str, Any]:
        """Fetch by ID, independent of any last-N activities sensor cache."""
        return await self._request("GET", activity_id)

    async def transform(
        self,
        activity_id: str,
        transform: Callable[[dict[str, Any]], dict[str, Any]],
        dry_run: bool,
        *,
        expected: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        validate_expected(expected)
        if type(dry_run) is not bool:
            raise ActivityError("invalid_dry_run")
        async with self._lock:
            before = await self.get(activity_id)
            if expected and any(before.get(key) != value for key, value in expected.items()):
                raise ActivityError("activity_changed_retry")
            changes = transform(before)
            validate_changes(changes)
            if "workout_type" in changes:
                sport = before.get("sport_type")
                allowed = (
                    (0, 1, 2, 3)
                    if sport in ("Run", "TrailRun", "VirtualRun")
                    else (10, 11, 12)
                    if sport in ("Ride", "MountainBikeRide", "GravelRide", "VirtualRide")
                    else ()
                )
                if changes["workout_type"] not in allowed:
                    raise ActivityError("invalid_workout_sport")
            changes = {
                key: value
                for key, value in changes.items()
                if not field_matches(key, before.get(key), value)
            }
            result = {
                "activity_id": str(activity_id),
                "changes": changes,
                "status": "would_update" if changes else "unchanged",
            }
            if dry_run or not changes:
                return result
            latest = await self.get(activity_id)
            if (
                (expected and any(latest.get(key) != value for key, value in expected.items()))
                or any(latest.get(key) != before.get(key) for key in changes)
                or (
                    "workout_type" in changes
                    and latest.get("sport_type") != before.get("sport_type")
                )
            ):
                raise ActivityError("activity_changed_retry")
            await self._request(
                "PUT",
                activity_id,
                data={
                    key: str(value).lower() if type(value) is bool else value
                    for key, value in changes.items()
                },
            )
            verified = await self.get(activity_id)
            if any(
                not field_matches(key, verified.get(key), value) for key, value in changes.items()
            ):
                raise ActivityError("verification_failed")
            return {**result, "status": "updated"}

    async def update(
        self,
        activity_id: str,
        changes: dict[str, Any],
        dry_run: bool = True,
        *,
        expected: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Preview by default; no full-object PUT that overwrites unrelated fields."""
        validate_changes(changes)
        return await self.transform(activity_id, lambda _: changes, dry_run, expected=expected)

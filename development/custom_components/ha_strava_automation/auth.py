"""Independent Strava auth using HA's native OAuth session and private storage.

Protocol references: developers.strava.com/docs/authentication/ and HA's
Application Credentials/OAuth2 framework. No provider integration code is reused.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from aiohttp import ClientError
from homeassistant.components.application_credentials import AuthImplementation
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    OAuth2TokenRequestConnectionError,
    OAuth2TokenRequestReauthError,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session

from .const import ATHLETE_URL, DOMAIN

KNOWN_SCOPES = frozenset(
    {
        "read",
        "read_all",
        "profile:read_all",
        "profile:write",
        "activity:read",
        "activity:read_all",
        "activity:write",
    }
)
REQUESTED_SCOPES = ("read", "activity:read_all", "activity:write", "profile:read_all")


class AccountVerificationError(Exception):
    """A stable category, never a provider body or credential-bearing exception."""


@dataclass
class AuthRuntime:
    """One native OAuth session owns refresh for an authenticated account."""

    session: OAuth2Session
    athlete_id: str
    client_id: str


def granted_scopes(value: Any) -> list[str]:
    """Validate callback scopes without treating malformed consent as permission."""
    if not isinstance(value, str) or not value.isascii() or len(value) > 1024:
        return []
    return sorted(set(value.replace(",", " ").split()) & KNOWN_SCOPES)


def has_activity_access(scopes: list[str]) -> bool:
    """All-activity read consent includes the ordinary activity-read capability."""
    return bool({"activity:read", "activity:read_all"} & set(scopes))


def _validated_token(value: Any) -> dict[str, Any]:
    """Keep only token fields; reject unusable tokens before native helper logs."""
    if not isinstance(value, dict):
        raise OAuth2TokenRequestConnectionError(domain=DOMAIN)
    if any(
        not isinstance(value.get(k), str) or not value[k].strip()
        for k in ("access_token", "refresh_token")
    ):
        raise OAuth2TokenRequestConnectionError(domain=DOMAIN)
    expires = value.get("expires_in")
    if type(expires) is not int or not 0 < expires <= 7 * 86400:
        raise OAuth2TokenRequestConnectionError(domain=DOMAIN)
    if str(value.get("token_type", "Bearer")).lower() != "bearer":
        raise OAuth2TokenRequestConnectionError(domain=DOMAIN)
    result = {
        "access_token": value["access_token"],
        "refresh_token": value["refresh_token"],
        "expires_in": expires,
        "token_type": "Bearer",
    }
    if "scope" in value:
        result["scope"] = " ".join(granted_scopes(value["scope"]))
    return result


class StravaOAuthImplementation(AuthImplementation):
    """Use native session locking/persistence with strict, sanitized token HTTP."""

    async def _token_request(self, data: dict) -> dict:
        """Exchange/refresh through documented form POSTs, with no raw-body logs."""
        payload = {**data, "client_id": self.client_id, "client_secret": self.client_secret}
        try:
            async with asyncio.timeout(20):
                async with async_get_clientsession(self.hass).post(
                    self.token_url, data=payload, allow_redirects=False
                ) as response:
                    if response.status != 200:
                        # A rejected exchange/refresh needs new consent/credentials.
                        # Do not rely on one provider error-body shape or log it.
                        if response.status in (400, 401, 403):
                            raise OAuth2TokenRequestReauthError(
                                domain=DOMAIN,
                                request_info=response.request_info,
                                status=response.status,
                            )
                        raise OAuth2TokenRequestConnectionError(domain=DOMAIN)
                    return _validated_token(await response.json())
        except OAuth2TokenRequestConnectionError, OAuth2TokenRequestReauthError:
            raise
        except ClientError, TimeoutError, ValueError:
            raise OAuth2TokenRequestConnectionError(domain=DOMAIN) from None


async def async_fetch_athlete(hass: HomeAssistant, access_token: str) -> str:
    """Verify identity against the authenticated athlete endpoint, not form data."""
    try:
        async with asyncio.timeout(20):
            async with async_get_clientsession(hass).get(
                ATHLETE_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                allow_redirects=False,
            ) as response:
                if response.status in (401, 403):
                    raise AccountVerificationError("invalid_auth")
                if response.status != 200:
                    raise AccountVerificationError("cannot_connect")
                body = await response.json()
    except AccountVerificationError:
        raise
    except ClientError, TimeoutError, ValueError:
        raise AccountVerificationError("cannot_connect") from None
    identifier = body.get("id") if isinstance(body, dict) else None
    if type(identifier) is not int or not 0 < identifier < 2**63:
        raise AccountVerificationError("invalid_account")
    return str(identifier)

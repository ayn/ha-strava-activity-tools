"""Explicit one-owner connection handover through native HA configuration APIs.

No provider authentication implementation is imported or copied. A loaded
source exposes a native HA OAuth2Session; we read that documented framework
object in HA memory, validate the account, and transfer ownership once.
"""

import math
import time
from dataclasses import dataclass, field

from aiohttp import ClientError
from homeassistant.components.application_credentials import (
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session, async_get_implementations
from homeassistant.helpers.storage import Store

from .auth import (
    AccountVerificationError,
    _validated_token,
    async_fetch_athlete,
    granted_scopes,
    has_activity_access,
)
from .client import ActivityError
from .const import DOMAIN
from .quota import async_get_budget
from .subscriptions import SubscriptionManager
from .webhook import strava_id


class MigrationError(Exception):
    """A stable user-facing migration category, never credentials."""


@dataclass
class MigrationSnapshot:
    source_entry_id: str
    athlete_id: str
    client_id: str
    data: dict = field(repr=False)
    client_secret: str = field(repr=False)


def migration_store(hass, source_entry_id):
    return Store(hass, 1, f"{DOMAIN}.migration.{source_entry_id}", private=True, atomic_writes=True)


async def async_prepare_migration(hass, source_entry_id):
    """Validate a selected live session, or resume a previously paused handover."""
    source = hass.config_entries.async_get_entry(source_entry_id)
    if source is None or source.domain != "ha_strava":
        raise MigrationError("unsupported_source")
    if any(
        entry.data.get("migration_source_entry_id") == source_entry_id
        or (source.unique_id is not None and entry.unique_id == source.unique_id)
        for entry in hass.config_entries.async_entries(DOMAIN)
    ):
        raise MigrationError("already_configured")
    for domain in ("strava_activity_tools", "activity_tools"):
        if any(
            (entry.disabled_by is None or entry.state is not ConfigEntryState.NOT_LOADED)
            and entry.data.get("source_entry_id") == source_entry_id
            for entry in hass.config_entries.async_entries(domain)
        ):
            raise MigrationError("dependent_integration_active")
    previous = await migration_store(hass, source_entry_id).async_load()
    if source.disabled_by is not None:
        if (
            source.disabled_by is not ConfigEntryDisabler.USER
            or not isinstance(previous, dict)
            or previous.get("source_entry_id") != source_entry_id
            or previous.get("phase") not in ("prepared", "paused")
        ):
            raise MigrationError("source_not_loaded")
        implementations = await async_get_implementations(hass, DOMAIN)
        implementation = implementations.get(previous.get("auth_implementation"))
        if implementation is None or implementation.client_id != previous.get("client_id"):
            raise MigrationError("migration_credentials_missing")
        session = OAuth2Session(hass, source, implementation)
    else:
        runtime = hass.data.get("ha_strava", {}).get(source_entry_id)
        session = getattr(runtime, "oauth_session", None)
        if not isinstance(session, OAuth2Session) or session.config_entry is not source:
            raise MigrationError("unsupported_source")
    try:
        client_id = getattr(session.implementation, "client_id", None)
        secret = getattr(session.implementation, "client_secret", None)
        if strava_id(client_id) != client_id or not isinstance(secret, str) or not secret:
            raise MigrationError("unsupported_source")
        if any(
            entry.data.get("client_id") == client_id
            for entry in hass.config_entries.async_entries(DOMAIN)
        ):
            raise MigrationError("shared_application_migration")
        for other in hass.config_entries.async_entries("ha_strava"):
            if other.entry_id == source_entry_id or other.disabled_by is not None:
                continue
            peer = hass.data.get("ha_strava", {}).get(other.entry_id)
            peer_session = getattr(peer, "oauth_session", None)
            if (
                not isinstance(peer_session, OAuth2Session)
                or peer_session.implementation.client_id == client_id
            ):
                raise MigrationError("shared_application_migration")
        await session.async_ensure_token_valid()
        token = _validated_token(session.token)
        expires = session.token.get("expires_at")
        if (
            type(expires) not in (int, float)
            or not math.isfinite(expires)
            or expires <= time.time()
        ):
            raise MigrationError("migration_auth_invalid")
        token["expires_at"] = expires
        scope = session.token.get("scope", source.data.get("scope", source.data.get("scopes")))
        if isinstance(scope, list) and all(isinstance(x, str) for x in scope):
            scope = " ".join(scope)
        scopes = granted_scopes(scope)
        if not has_activity_access(scopes):
            raise MigrationError("migration_scope_unknown")
        token["scope"] = " ".join(scopes)
        budget = await async_get_budget(hass, client_id)
        athlete_id = await async_fetch_athlete(hass, token["access_token"], budget)
        verifier = SubscriptionManager(hass, client_id, secret, None, budget)
        remote = await verifier.request("GET")
        if not isinstance(remote, list) or len(remote) > 1:
            raise MigrationError("migration_subscription_unknown")
        subscription = None
        if remote:
            item = remote[0]
            callbacks = [
                value.rstrip("/")
                for mapping in (source.data, source.options)
                for key, value in mapping.items()
                if "callback" in key.lower() and isinstance(value, str)
            ]
            if (
                not isinstance(item, dict)
                or strava_id(item.get("id")) is None
                or not isinstance(item.get("callback_url"), str)
                or item["callback_url"].rstrip("/") not in callbacks
            ):
                raise MigrationError("migration_subscription_unknown")
            subscription = {"id": strava_id(item["id"]), "callback_url": item["callback_url"]}
    except MigrationError:
        raise
    except (
        AccountVerificationError,
        ActivityError,
        OSError,
        ValueError,
        ClientError,
        TimeoutError,
        KeyError,
        TypeError,
        AttributeError,
    ):
        raise MigrationError("migration_auth_invalid") from None
    if source.unique_id is not None and source.unique_id != athlete_id:
        raise MigrationError("wrong_account")
    if any(entry.unique_id == athlete_id for entry in hass.config_entries.async_entries(DOMAIN)):
        raise MigrationError("already_configured")
    return MigrationSnapshot(
        source_entry_id,
        athlete_id,
        client_id,
        {
            "athlete_id": athlete_id,
            "client_id": client_id,
            "token": token,
            "granted_scopes": scopes,
            "migration_source_entry_id": source_entry_id,
            "migration_remote_subscription": subscription,
        },
        secret,
    )


async def _credential(hass, snapshot):
    implementations = await async_get_implementations(hass, DOMAIN)
    matches = [item for item in implementations.values() if item.client_id == snapshot.client_id]
    if len(matches) > 1 or (matches and matches[0].client_secret != snapshot.client_secret):
        raise MigrationError("migration_credentials_conflict")
    if matches:
        return matches[0].domain
    name = f"{DOMAIN}_{snapshot.client_id}"
    await async_import_client_credential(
        hass,
        DOMAIN,
        ClientCredential(snapshot.client_id, snapshot.client_secret, "Strava Automation app"),
        auth_domain=name,
    )
    return name


async def async_pause_source(hass, snapshot):
    """Record recoverable intent before disabling the sole old refresh owner."""
    source = hass.config_entries.async_get_entry(snapshot.source_entry_id)
    if source is None or source.domain != "ha_strava" or source.supports_unload is False:
        raise MigrationError("unsupported_source")
    implementation = await _credential(hass, snapshot)
    snapshot.data["auth_implementation"] = implementation
    store = migration_store(hass, source.entry_id)
    record = {
        "source_entry_id": source.entry_id,
        "client_id": snapshot.client_id,
        "athlete_id": snapshot.athlete_id,
        "auth_implementation": implementation,
        "phase": "prepared",
    }
    await store.async_save(record)
    if source.disabled_by is None:
        paused = await hass.config_entries.async_set_disabled_by(
            source.entry_id, ConfigEntryDisabler.USER
        )
        if not paused or source.state is not ConfigEntryState.NOT_LOADED:
            await hass.config_entries.async_set_disabled_by(source.entry_id, None)
            raise MigrationError("migration_pause_failed")
    if (
        source.disabled_by is not ConfigEntryDisabler.USER
        or source.state is not ConfigEntryState.NOT_LOADED
    ):
        raise MigrationError("migration_pause_failed")
    current = source.data["token"]
    token = _validated_token(current)
    token["expires_at"] = current["expires_at"]
    token.setdefault("scope", snapshot.data["token"]["scope"])
    snapshot.data["token"] = token
    try:
        await store.async_save({**record, "phase": "paused"})
    except OSError:
        # No target has been created/refreshed/written yet: this rollback is safe.
        await hass.config_entries.async_set_disabled_by(source.entry_id, None)
        raise MigrationError("migration_storage_failed") from None


async def async_takeover_for_entry(hass, entry):
    """A callback replacement is allowed only for the confirmed paused source."""
    identifier = entry.data.get("migration_source_entry_id")
    if identifier is None:
        return None
    source = hass.config_entries.async_get_entry(identifier)
    record = await migration_store(hass, identifier).async_load()
    if (
        source is None
        or source.domain != "ha_strava"
        or source.disabled_by is not ConfigEntryDisabler.USER
        or source.state is not ConfigEntryState.NOT_LOADED
        or not isinstance(record, dict)
        or record.get("phase") not in ("prepared", "paused")
        or record.get("client_id") != entry.data["client_id"]
        or record.get("athlete_id") != entry.data["athlete_id"]
    ):
        return None
    return entry.data.get("migration_remote_subscription")


async def async_mark_migration_complete(hass, entry):
    identifier = entry.data.get("migration_source_entry_id")
    if identifier is None:
        return
    store = migration_store(hass, identifier)
    record = await store.async_load()
    if (
        isinstance(record, dict)
        and record.get("client_id") == entry.data["client_id"]
        and record.get("athlete_id") == entry.data["athlete_id"]
    ):
        await store.async_save({**record, "phase": "active", "target_entry_id": entry.entry_id})

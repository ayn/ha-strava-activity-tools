"""Independent Strava authentication and native activity automation runtime."""

import logging

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.config_entry_oauth2_flow import (
    OAuth2Session,
    async_get_config_entry_implementation,
)
from homeassistant.helpers.network import NoURLAvailableError

from .auth import (
    AccountVerificationError,
    AuthRuntime,
    async_fetch_athlete,
    granted_scopes,
    has_activity_access,
)
from .client import ActivityError
from .const import DOMAIN, READY_EVENT, WORKOUT_EVENT
from .quota import async_get_budget

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register shared actions once for every account."""
    from .actions import async_setup as setup_actions

    return await setup_actions(hass, config)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Own a native session and verify identity before exposing a runtime."""
    implementation = await async_get_config_entry_implementation(hass, entry)
    if implementation.client_id != entry.data.get("client_id"):
        raise ConfigEntryAuthFailed("Application credentials do not match configured account")
    session = OAuth2Session(hass, entry, implementation)
    await session.async_ensure_token_valid()
    if not has_activity_access(granted_scopes(session.token.get("scope"))):
        raise ConfigEntryAuthFailed("missing_activity_scope")
    try:
        budget = await async_get_budget(hass, implementation.client_id)
        athlete_id = await async_fetch_athlete(hass, session.token["access_token"], budget)
    except AccountVerificationError as err:
        if str(err) == "cannot_connect":
            raise ConfigEntryNotReady("Account verification temporarily unavailable") from None
        raise ConfigEntryAuthFailed("Account verification failed") from None
    except ActivityError, OSError, ValueError:
        raise ConfigEntryNotReady(
            "Strava account verification is temporarily unavailable"
        ) from None
    if athlete_id != entry.unique_id or athlete_id != entry.data.get("athlete_id"):
        raise ConfigEntryAuthFailed("Authenticated athlete does not match configured account")
    entry.runtime_data = AuthRuntime(session, athlete_id, implementation.client_id)
    try:
        await async_setup_features(hass, entry)
    except NoURLAvailableError:
        raise ConfigEntryNotReady(
            "Configure a public HTTPS Home Assistant URL or HA Cloud webhook"
        ) from None
    except ActivityError, OSError, ValueError, ClientError, TimeoutError:
        raise ConfigEntryNotReady(
            "Strava setup needs connection or private-storage recovery"
        ) from None
    return True


async def async_setup_features(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Load account-local journals/jobs without a provider runtime dependency."""
    from .actions import Runtime, activity_fields, bind_source, environment_task, workout_suggestion
    from .client import ActivityClient, ActivityError, field_matches
    from .inbox import InboxPump
    from .journal import RunJournal
    from .muting import mute_activity
    from .session import HASession
    from .subscriptions import async_get_application
    from .worker import JobWorker

    auth = entry.runtime_data
    runtime = Runtime(
        auth.session,
        auth.athlete_id,
        auth.client_id,
        ActivityClient(HASession(auth.session), auth.athlete_id),
        entry.entry_id,
    )
    runtime.application = await async_get_application(hass, entry, auth.session)
    runtime.client.session = HASession(auth.session, runtime.application.budget)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    entry.runtime_data = runtime
    runtime.journal = RunJournal(hass, entry)

    async def execute(job: dict) -> dict:
        # A due timer can overlap disable/unload: recheck the current owner first.
        bind_source(hass, runtime)
        if (
            job["kind"] != "fetch"
            and not job["parameters"].get("dry_run", False)
            and entry.options.get("preview_only", True)
        ):
            raise ActivityError("writes_disabled")
        if job["kind"] == "mute":
            return await mute_activity(runtime.client, job["activity_id"], job["parameters"])
        if job["kind"] == "environment":
            return await environment_task(
                hass, runtime.client, job["activity_id"], job["parameters"]
            )
        if job["kind"] == "update":
            params = job["parameters"]
            current = await runtime.client.get(job["activity_id"])
            if all(field_matches(k, current.get(k), v) for k, v in params["changes"].items()):
                return {"status": "unchanged", "activity_id": job["activity_id"], "changes": {}}
            return await runtime.client.update(
                job["activity_id"],
                params["changes"],
                params["dry_run"],
                expected=params.get("expected"),
            )
        if job["kind"] == "workout":
            suggestion = await workout_suggestion(
                hass, entry, runtime, job["activity_id"], job["parameters"]["calendar_entity"]
            )
            changes = {
                **suggestion.get("changes", {}),
                **suggestion.get("track_shoes", {}).get("changes", {}),
            }
            result = (
                (
                    await runtime.client.update(
                        job["activity_id"], changes, False, expected=suggestion["expected"]
                    )
                )
                if changes
                else {"status": "unchanged", "changes": {}}
            )
            hass.bus.async_fire(
                WORKOUT_EVENT,
                {"config_entry_id": entry.entry_id, "activity_id": job["activity_id"]},
            )
            return {**suggestion, **result, "activity_id": job["activity_id"]}
        activity = await runtime.client.get(job["activity_id"])
        hass.bus.async_fire(
            READY_EVENT, {"config_entry_id": entry.entry_id, **activity_fields(activity)}
        )
        return {"status": "delivered"}

    runtime.worker = JobWorker(hass, entry, execute, runtime.journal, start_paused=True)
    try:
        await runtime.journal.load()
        await runtime.worker.load()
        runtime.inbox = InboxPump(hass, entry, runtime.worker)
        await runtime.inbox.load()
        await hass.config_entries.async_forward_entry_setups(entry, [Platform.SENSOR])
        if entry.options.get("webhook_enabled", True):
            from .migration import async_takeover_for_entry

            takeover = await async_takeover_for_entry(hass, entry)
            await runtime.application.connect(
                runtime.athlete_id, runtime.inbox.accept, takeover=takeover
            )
    except Exception:
        if runtime.inbox is not None:
            await runtime.inbox.close()
        await runtime.application.disconnect(runtime.athlete_id)
        await hass.config_entries.async_unload_platforms(entry, [Platform.SENSOR])
        await runtime.worker.close()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        entry.runtime_data = None
        raise
    from .migration import async_mark_migration_complete

    try:
        await async_mark_migration_complete(hass, entry)
    except OSError:
        _LOGGER.warning(
            "Migration audit checkpoint could not be saved; credential ownership is unchanged"
        )
    runtime.worker.resume()
    entry.async_on_unload(entry.add_update_listener(async_options_updated))


async def async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is None or entry.options.get("preview_only", True):
        return
    for identifier, job in list(runtime.worker.queue.jobs.items()):
        if job["status"] == "failed" and job["last_error"] == "writes_disabled":
            await runtime.worker.queue.replay(identifier)
    runtime.worker.arm()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Release the local runtime without revoking tokens or touching subscriptions."""
    if not await hass.config_entries.async_unload_platforms(entry, [Platform.SENSOR]):
        return False
    runtime = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if runtime is not None:
        await runtime.application.disconnect(runtime.athlete_id)
        await runtime.inbox.close()
        await runtime.worker.close()
    entry.runtime_data = None
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete only this entry's local data; shared-app callbacks outlive peers."""
    from homeassistant.components import persistent_notification
    from homeassistant.helpers.storage import Store

    peers = [
        peer
        for peer in hass.config_entries.async_entries(DOMAIN)
        if peer.entry_id != entry.entry_id
        and peer.data.get("client_id") == entry.data.get("client_id")
    ]
    if not peers:
        manager = hass.data.get(f"{DOMAIN}_applications", {}).get(entry.data.get("client_id"))
        if manager is not None:
            try:
                await manager.remove()
            except ActivityError, OSError:
                persistent_notification.async_create(
                    hass,
                    "The account was removed locally. Its remote webhook could not "
                    "be verified and removed. Reconnect this API app to manage its subscription.",
                    title="Strava Automation: webhook cleanup needs attention",
                    notification_id=f"{DOMAIN}_subscription_cleanup",
                )
    for suffix in ("jobs", "history", "inbox"):
        await Store(
            hass, 1, f"{DOMAIN}.{entry.entry_id}.{suffix}", private=True, atomic_writes=True
        ).async_remove()

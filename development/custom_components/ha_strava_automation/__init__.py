"""Independent Strava authentication and native activity automation runtime."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.config_entry_oauth2_flow import (
    OAuth2Session,
    async_get_config_entry_implementation,
)

from .auth import (
    AccountVerificationError,
    AuthRuntime,
    async_fetch_athlete,
    granted_scopes,
    has_activity_access,
)
from .const import DOMAIN, READY_EVENT, WORKOUT_EVENT
from .quota import async_get_budget


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
    if athlete_id != entry.unique_id or athlete_id != entry.data.get("athlete_id"):
        raise ConfigEntryAuthFailed("Authenticated athlete does not match configured account")
    entry.runtime_data = AuthRuntime(session, athlete_id, implementation.client_id)
    await async_setup_features(hass, entry)
    return True


async def async_setup_features(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Load account-local journals/jobs without a provider runtime dependency."""
    from .actions import Runtime, activity_fields, environment_task, workout_suggestion
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

    runtime.worker = JobWorker(hass, entry, execute, runtime.journal)
    try:
        await runtime.journal.load()
        await runtime.worker.load()
        runtime.inbox = InboxPump(hass, entry, runtime.worker)
        await runtime.inbox.load()
        await hass.config_entries.async_forward_entry_setups(entry, [Platform.SENSOR])
        if entry.options.get("webhook_enabled", True):
            await runtime.application.connect(runtime.athlete_id, runtime.inbox.accept)
    except Exception:
        if runtime.inbox is not None:
            await runtime.inbox.close()
        await runtime.application.disconnect(runtime.athlete_id)
        await hass.config_entries.async_unload_platforms(entry, [Platform.SENSOR])
        await runtime.worker.close()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        entry.runtime_data = None
        raise


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

"""Home Assistant companion for native activity automations; no polling or token store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import Event, HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .client import ActivityClient, ActivityError
from .const import DOMAIN, ERROR_EVENT, READY_EVENT, SOURCE_DOMAIN, SOURCE_EVENT
from .core import METERS_PER_MILE, match_activity, nonnegative
from .enrichment import enrich_description
from .environment import environment_eligible, missing_environment
from .history import async_readings
from .journal import RunJournal, journal_store
from .muting import mute_activity, mute_parameters
from .session import HASession
from .tracks import track_evidence
from .weather import CREDIT, async_weather_readings
from .worker import JobWorker, job_store
from .workouts import calendar_window, suggest_track_shoes, suggest_workout

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.SENSOR]
FILTER_SCHEMA = vol.Schema(
    {
        vol.Optional("name_contains"): str,
        vol.Optional("name_exact"): str,
        vol.Optional("device_contains"): str,
        vol.Optional("device_exact"): str,
        vol.Optional("sport_types"): [str],
        vol.Optional("distance_below_miles"): vol.All(vol.Coerce(float), vol.Range(min=0)),
    }
)


@dataclass
class Runtime:
    """One OAuth adapter and one account-scoped durable worker."""

    client: ActivityClient
    source_entry_id: str
    worker: JobWorker | None = None
    journal: RunJournal | None = None


def activity_fields(activity: dict[str, Any]) -> dict[str, Any]:
    """Only fields needed for native HA conditions; no routes, photos, or tokens."""
    distance = nonnegative(activity.get("distance"))
    return {
        "activity_id": str(activity["id"]),
        "athlete_id": str(activity["athlete"]["id"]),
        "name": activity.get("name", ""),
        "device_name": activity.get("device_name", ""),
        "sport_type": activity.get("sport_type", ""),
        "distance_m": distance,
        "distance_miles": distance / METERS_PER_MILE if distance is not None else None,
        "start_date": activity.get("start_date"),
        "trainer": bool(activity.get("trainer")),
        "manual": bool(activity.get("manual")),
    }


def environment_parameters(data: dict[str, Any]) -> dict[str, Any]:
    """New callers need only an account/activity; legacy sensor calls keep their intents."""
    source = data.get("weather_source")
    if source == "location_api" or (
        source is None and not any(key in data for key in ("aqi_entity", "uv_entity"))
    ):
        return {"weather_source": "location_api", "dry_run": data.get("dry_run", True)}
    if not all(data.get(key) for key in ("aqi_entity", "uv_entity")):
        raise ActivityError("invalid_weather_config")
    params = {key: data[key] for key in ("aqi_entity", "uv_entity", "radius_km", "dry_run")}
    if data.get("external_fallback", False):
        params["external_fallback"] = True
    return params


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register actions once; responses remain available in native automation traces."""
    hass.data.setdefault(DOMAIN, {})
    common = {vol.Required("config_entry_id"): str, vol.Required("activity_id"): cv.string}

    async def execute_action(call: ServiceCall) -> dict[str, Any]:
        runtime = hass.data[DOMAIN].get(call.data["config_entry_id"])
        if runtime is None:
            raise HomeAssistantError("source_not_loaded")
        entry = hass.config_entries.async_get_entry(call.data["config_entry_id"])
        preview = bool(
            entry is None
            or entry.options.get("preview_only", True)
            or call.data.get("dry_run", True)
        )
        try:
            if call.service == "list_jobs":
                return runtime.worker.status()
            if call.service == "replay_job":
                return await runtime.worker.replay(call.data["job_id"])
            if call.service == "mute_activity":
                params = mute_parameters({"filters": call.data["filters"], "dry_run": preview})
                if not preview:
                    return await runtime.worker.submit("mute", call.data["activity_id"], params)
                bind_source(hass, runtime)
                return await mute_activity(runtime.client, call.data["activity_id"], params)
            if call.service == "append_environment" and not preview:
                params = environment_parameters(call.data)
                return await runtime.worker.submit("environment", call.data["activity_id"], params)
            client = runtime.client
            bind_source(hass, runtime)
            activity_id = call.data["activity_id"]
            if call.service == "get_activity":
                return activity_fields(await client.get(activity_id))
            if call.service == "detect_track":
                activity = await client.get(activity_id)
                evidence = await hass.async_add_executor_job(track_evidence, activity)
                return {
                    "activity_id": activity_id,
                    "likely_track": evidence["on_track"],
                    **evidence,
                    "method": "gps_geometry",
                    "preview_only": True,
                }
            if call.service == "update_activity":
                kwargs = (
                    {"expected": dict(call.data["expected"])} if "expected" in call.data else {}
                )
                return await client.update(
                    activity_id, dict(call.data["changes"]), preview, **kwargs
                )
            if call.service == "suggest_workout":
                activity = await client.get(activity_id)
                calendar = call.data["calendar_entity"]
                try:
                    start, end = calendar_window(activity, hass.config.time_zone)
                except KeyError, TypeError, ValueError:
                    raise ActivityError("invalid_activity_time") from None
                try:
                    response = await hass.services.async_call(
                        "calendar",
                        "get_events",
                        {
                            "entity_id": calendar,
                            "start_date_time": start.isoformat(),
                            "end_date_time": end.isoformat(),
                        },
                        blocking=True,
                        return_response=True,
                    )
                except HomeAssistantError:
                    raise ActivityError("calendar_unavailable") from None
                events = (response or {}).get(calendar, {}).get("events")
                if not isinstance(events, list):
                    raise ActivityError("calendar_unavailable")
                selected = entry.options.get("track_shoes", "disabled")
                workout = suggest_workout(activity, events, hass.config.time_zone)
                geometry = await hass.async_add_executor_job(track_evidence, activity)
                try:
                    shoes = await client.shoes() if selected != "disabled" else []
                    track = await hass.async_add_executor_job(
                        suggest_track_shoes,
                        activity,
                        events,
                        hass.config.time_zone,
                        selected,
                        shoes,
                    )
                except ActivityError:
                    track = {
                        "status": "abstained",
                        "changes": {},
                        "reason": "shoe_catalog_unavailable",
                    }
                return {
                    "activity_id": activity_id,
                    "preview_only": True,
                    "expected": {
                        key: activity.get(key)
                        for key in ("sport_type", "workout_type", "gear_id", "trainer", "manual")
                    },
                    **workout,
                    "is_workout": activity.get("workout_type") == 3
                    or workout["status"] == "suggested",
                    **geometry,
                    "track_shoes": track,
                }
            if call.service == "match_activity":
                activity = await client.get(activity_id)
                try:
                    filters = FILTER_SCHEMA(dict(call.data.get("filters", {})))
                except vol.Invalid as err:
                    raise ActivityError("invalid_filters") from err
                return {"matches": match_activity(activity, **filters), **activity_fields(activity)}
            params = environment_parameters(call.data)
            if preview:
                return await environment_task(
                    hass, client, activity_id, {**params, "dry_run": True}
                )
            return await runtime.worker.submit("environment", activity_id, params)
        except ActivityError as err:
            raise HomeAssistantError(err.category) from None

    async def handle(call: ServiceCall) -> dict[str, Any]:
        runtime = hass.data[DOMAIN].get(call.data["config_entry_id"])
        entry = hass.config_entries.async_get_entry(call.data["config_entry_id"])
        preview = (
            entry is None
            or entry.options.get("preview_only", True)
            or call.data.get("dry_run", True)
            or call.service in ("match_activity", "detect_track", "suggest_workout")
        )
        record = (
            runtime is not None
            and runtime.journal is not None
            and call.service
            in (
                "match_activity",
                "detect_track",
                "suggest_workout",
                "update_activity",
                "mute_activity",
                "append_environment",
            )
        )
        try:
            result = await execute_action(call)
        except HomeAssistantError as err:
            # A rejected foreign-owner response must never expose that activity in history.
            if record and str(err) != "wrong_athlete":
                runtime.journal.record(
                    call.service,
                    call.data["activity_id"],
                    preview,
                    {
                        "status": "failed",
                        "error": str(err),
                    },
                )
            raise
        if record and (call.service not in ("append_environment", "mute_activity") or preview):
            if call.service == "detect_track":
                result_for_history = {
                    **result,
                    "status": "track" if result["on_track"] else "not_track",
                }
            else:
                result_for_history = result
            runtime.journal.record(
                call.service, call.data["activity_id"], preview, result_for_history
            )
        return result

    for name, extra in {
        "mute_activity": {
            vol.Required("filters"): dict,
            vol.Optional("dry_run", default=True): cv.boolean,
        },
        "get_activity": {},
        "detect_track": {},
        "match_activity": {vol.Optional("filters", default={}): dict},
        "suggest_workout": {
            vol.Required("calendar_entity"): vol.All(cv.entity_id, vol.Match(r"^calendar\.")),
        },
        "update_activity": {
            vol.Required("changes"): dict,
            vol.Optional("expected"): dict,
            vol.Optional("dry_run", default=True): cv.boolean,
        },
        "append_environment": {
            vol.Optional("weather_source"): vol.In(("location_api", "ha_history")),
            vol.Optional("aqi_entity"): cv.entity_id,
            vol.Optional("uv_entity"): cv.entity_id,
            vol.Optional("radius_km", default=15): vol.All(
                vol.Coerce(float), vol.Range(min=0, max=100)
            ),
            vol.Optional("dry_run", default=True): cv.boolean,
            vol.Optional("external_fallback", default=False): cv.boolean,
        },
    }.items():
        hass.services.async_register(
            DOMAIN,
            name,
            handle,
            schema=vol.Schema({**common, **extra}),
            supports_response=SupportsResponse.OPTIONAL,
        )
    for name, extra in {"list_jobs": {}, "replay_job": {vol.Required("job_id"): str}}.items():
        hass.services.async_register(
            DOMAIN,
            name,
            handle,
            schema=vol.Schema({vol.Required("config_entry_id"): str, **extra}),
            supports_response=SupportsResponse.OPTIONAL,
        )
    return True


def bind_source(hass: HomeAssistant, runtime: Runtime) -> None:
    coordinator = hass.data.get(SOURCE_DOMAIN, {}).get(runtime.source_entry_id)
    if coordinator is None:
        raise ActivityError("source_not_loaded")
    runtime.client.session = HASession(coordinator.oauth_session)


async def environment_task(
    hass: HomeAssistant, client: ActivityClient, activity_id: str, params: dict
) -> dict:
    activity = await client.get(activity_id)
    now = dt_util.utcnow()
    api = params.get("weather_source") == "location_api"
    external = api or params.get("external_fallback", False)
    if not environment_eligible(activity, now, activity.get("start_latlng"), 0):
        return {"activity_id": activity_id, "status": "skipped_ineligible", "changes": {}}
    home = None if api else hass.states.get("zone.home")
    if home is None and not external:
        raise ActivityError("home_location_missing")
    local = home is not None and environment_eligible(
        activity,
        now,
        [home.attributes.get("latitude"), home.attributes.get("longitude")],
        params["radius_km"],
    )
    if not local and not external:
        return {"activity_id": activity_id, "status": "skipped_ineligible", "changes": {}}
    needed = missing_environment(activity.get("description"))
    readings = [None, None]
    if local:
        try:
            readings = await async_readings(
                hass, activity["start_date"], [params["aqi_entity"], params["uv_entity"]]
            )
        except ActivityError:
            if not external and any(needed):
                raise
    source = None
    if external and any(need and readings[i] is None for i, need in enumerate(needed)):
        weather = await async_weather_readings(
            hass, activity["start_date"], activity["start_latlng"]
        )
        for i, need in enumerate(needed):
            if need and readings[i] is None:
                readings[i] = weather[i]
                if readings[i] is None:
                    raise ActivityError("weather_unavailable")
        source = CREDIT
    aqi, uv = readings
    result = await enrich_description(
        client, activity_id, aqi, uv, params["dry_run"], **({"source": source} if source else {})
    )
    return {**result, "aqi": aqi, "uv": uv, "start_time": activity["start_date"]}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Reuse the loaded source's OAuth session and patched create event."""
    source_id = entry.data["source_entry_id"]
    coordinator = hass.data.get(SOURCE_DOMAIN, {}).get(source_id)
    if coordinator is None or not hasattr(coordinator, "oauth_session"):
        raise ConfigEntryNotReady("source_not_loaded")
    runtime = Runtime(
        ActivityClient(HASession(coordinator.oauth_session), entry.data["athlete_id"]), source_id
    )
    hass.data[DOMAIN][entry.entry_id] = runtime
    runtime.journal = RunJournal(hass, entry)
    await runtime.journal.load()

    async def execute(job: dict) -> dict:
        if (
            job["kind"] in ("environment", "mute")
            and not job["parameters"]["dry_run"]
            and entry.options.get("preview_only", True)
        ):
            raise ActivityError("writes_disabled")
        bind_source(hass, runtime)
        if job["kind"] == "mute":
            return await mute_activity(runtime.client, job["activity_id"], job["parameters"])
        if job["kind"] == "environment":
            return await environment_task(
                hass, runtime.client, job["activity_id"], job["parameters"]
            )
        activity = await runtime.client.get(job["activity_id"])
        hass.bus.async_fire(
            READY_EVENT, {"config_entry_id": entry.entry_id, **activity_fields(activity)}
        )
        return {"status": "delivered"}

    runtime.worker = JobWorker(hass, entry, execute, runtime.journal)
    try:
        if "legacy_entry_id" in entry.data:
            from .migration import migrate_legacy_receipts

            await migrate_legacy_receipts(hass, entry)
        await runtime.worker.load()
        if "legacy_entry_id" in entry.data:
            hass.config_entries.async_update_entry(
                entry,
                data={key: value for key, value in entry.data.items() if key != "legacy_entry_id"},
            )
    except (OSError, ActivityError, KeyError, TypeError, ValueError) as err:
        await runtime.worker.close()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise ConfigEntryNotReady("Job storage needs attention; refusing to reset it") from err

    async def enqueue(activity_id: str) -> None:
        try:
            await runtime.worker.submit("fetch", activity_id)
        except OSError, ActivityError:
            hass.bus.async_fire(
                ERROR_EVENT,
                {
                    "config_entry_id": entry.entry_id,
                    "activity_id": activity_id,
                    "category": "job_enqueue_failed",
                },
            )

    @callback
    def receive(event: Event) -> None:
        if str(event.data.get("athlete_id")) != str(entry.data["athlete_id"]):
            return
        activity_id = event.data.get("activity_id")
        if isinstance(activity_id, bool):
            return
        activity_id = str(activity_id or "")
        if not activity_id.isascii() or not activity_id.isdecimal() or not activity_id.lstrip("0"):
            return
        entry.async_create_background_task(hass, enqueue(activity_id), f"{DOMAIN}_enqueue")

    entry.async_on_unload(hass.bus.async_listen(SOURCE_EVENT, receive))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """HA cancels entry background tasks and registered listeners on unload."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    runtime = hass.data[DOMAIN].pop(entry.entry_id, None)
    if runtime and runtime.worker:
        await runtime.worker.close()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove private pending intents and receipts when their account is removed."""
    await job_store(hass, entry.entry_id).async_remove()
    await journal_store(hass, entry.entry_id).async_remove()

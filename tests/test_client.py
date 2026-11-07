"""Contract tests use fake OAuth, never live Strava credentials."""

from copy import deepcopy
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.strava_activity_tools.client import ActivityClient, ActivityError
from custom_components.strava_activity_tools.enrichment import enrich_description


@pytest.fixture
def api():
    class FakeSession:
        def __init__(self):
            self.activity = {
                "id": 9,
                "athlete": {"id": 42},
                "name": "Run",
                "description": "notes",
                "hide_from_home": False,
                "gear_id": "g1",
            }
            self.calls = []
            self.status = 200
            self.headers = {}
            self.apply = True

        async def async_request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            if method == "PUT" and self.apply:
                self.activity.update(
                    {
                        key: value == "true"
                        if key in ("hide_from_home", "trainer", "commute")
                        else value
                        for key, value in kwargs["data"].items()
                    }
                )
            response = AsyncMock()
            response.release = Mock()
            response.status = self.status
            response.headers = self.headers
            response.json.return_value = deepcopy(self.activity)
            return response

    return FakeSession()


def writes(api):
    return [call for call in api.calls if call[0] == "PUT"]


async def test_dry_run_is_default_and_never_writes(api):
    result = await ActivityClient(api, "42").update("9", {"hide_from_home": True})
    assert result["status"] == "would_update"
    assert result["changes"] == {"hide_from_home": True}
    assert not writes(api)


async def test_update_verifies_and_second_delivery_is_noop(api):
    client = ActivityClient(api, "42")
    assert (await client.update("9", {"hide_from_home": True}, False))["status"] == "updated"
    assert (await client.update("9", {"hide_from_home": True}, False))["status"] == "unchanged"
    assert len(writes(api)) == 1


async def test_owner_check_before_any_write(api):
    with pytest.raises(ActivityError, match="wrong_athlete"):
        await ActivityClient(api, "99").update("9", {"name": "changed"}, False)
    assert not writes(api)


@pytest.mark.parametrize("activity_id", ["../athlete", "9?x=1", "-9", "", True])
async def test_id_cannot_change_url(api, activity_id):
    with pytest.raises(ActivityError, match="invalid_activity_id"):
        await ActivityClient(api, "42").get(activity_id)
    assert not api.calls


@pytest.mark.parametrize(
    "changes",
    [
        {"private": True},
        {"access_token": "x"},
        {"hide_from_home": "false"},
        {"description": None},
        {"gear_id": "../../"},
        {"name": 42},
        {"commute": 1},
        {},
    ],
)
async def test_allowlisted_typed_changes(api, changes):
    with pytest.raises(ActivityError, match="invalid_changes"):
        await ActivityClient(api, "42").update("9", changes, False)
    assert not api.calls


@pytest.mark.parametrize(
    ("status", "category"),
    [
        (401, "reauth_required"),
        (403, "write_scope_or_access_denied"),
        (404, "activity_not_found"),
        (429, "rate_limited"),
        (500, "upstream_unavailable"),
    ],
)
async def test_error_category_does_not_leak_response(api, status, category):
    api.status = status
    api.headers = {"Retry-After": "900"}
    with pytest.raises(ActivityError, match=category) as error:
        await ActivityClient(api, "42").get("9")
    if status == 429:
        assert error.value.retry_after == 900


async def test_description_change_during_read_prevents_overwrite(api):
    original = api.async_request
    gets = 0

    async def racing(method, url, **kwargs):
        nonlocal gets
        if method == "GET":
            gets += 1
            if gets == 2:
                api.activity["description"] = "manually edited"
        return await original(method, url, **kwargs)

    api.async_request = racing
    with pytest.raises(ActivityError, match="activity_changed_retry"):
        await enrich_description(ActivityClient(api, "42"), "9", 35, 0, False)
    assert not writes(api)


async def test_enrich_is_idempotent_preserves_notes_and_handles_zero(api):
    client = ActivityClient(api, "42")
    result = await enrich_description(client, "9", 35, 0, False)
    assert result["status"] == "updated"
    assert api.activity["description"] == "notes\n\nAQI 35 💚\nUV Index 0 🟢"
    assert (await enrich_description(client, "9", 35, 0, False))["status"] == "unchanged"
    assert len(writes(api)) == 1


async def test_unverified_write_is_reported(api):
    api.apply = False
    with pytest.raises(ActivityError, match="verification_failed"):
        await ActivityClient(api, "42").update("9", {"name": "new"}, False)


async def test_missing_history_must_not_write(api):
    with pytest.raises(ActivityError, match="history_unavailable"):
        await enrich_description(ActivityClient(api, "42"), "9", None, 0, False)
    assert not writes(api)


async def test_writes_use_documented_form_fields(api):
    await ActivityClient(api, "42").update("9", {"hide_from_home": True}, False)
    assert writes(api)[0][2]["data"] == {"hide_from_home": "true"}


async def test_oauth_refresh_failure_becomes_reauth_error(api):
    from homeassistant.helpers.config_entry_oauth2_flow import OAuth2TokenRequestReauthError

    api.async_request = AsyncMock(
        side_effect=OAuth2TokenRequestReauthError(
            domain="ha_strava", request_info=None, history=(), status=400, message="invalid_grant"
        )
    )
    from custom_components.strava_activity_tools.session import HASession

    with pytest.raises(ActivityError, match="reauth_required"):
        await ActivityClient(HASession(api), "42").get("9")


async def test_cleared_gear_uses_api_none_but_verifies_null(api):
    original = api.async_request

    async def clear(method, url, **kwargs):
        result = await original(method, url, **kwargs)
        if method == "PUT" and api.activity.get("gear_id") == "none":
            api.activity["gear_id"] = None
        return result

    api.async_request = clear
    client = ActivityClient(api, "42")
    assert (await client.update("9", {"gear_id": "none"}, False))["status"] == "updated"
    assert (await client.update("9", {"gear_id": "none"}, False))["status"] == "unchanged"
    assert len(writes(api)) == 1


async def test_concurrent_annotation_and_gear_preserve_each_other(api):
    import asyncio

    client = ActivityClient(api, "42")
    await asyncio.gather(
        enrich_description(client, "9", 40, 0, False), client.update("9", {"gear_id": "g2"}, False)
    )
    assert api.activity["gear_id"] == "g2"
    assert "AQI 40 💚" in api.activity["description"]


@pytest.mark.parametrize(
    "manual_changes",
    [
        {"name": "Track intervals"},
        {"gear_id": "g99"},
        {"workout_type": 3},
        {"description_prefix": "Updated notes after the run\n\n"},
        {"description_suffix": "\n\nFelt good on the last rep."},
    ],
)
async def test_annotation_after_manual_edit_and_client_reload_is_noop(api, manual_changes):
    """Remote markers protect edits even without an in-memory dedupe receipt."""
    await enrich_description(ActivityClient(api, "42"), "9", 35, 0, False)
    changes = dict(manual_changes)
    prefix = changes.pop("description_prefix", "")
    suffix = changes.pop("description_suffix", "")
    api.activity.update(changes)
    api.activity["description"] = prefix + api.activity["description"] + suffix
    edited = deepcopy(api.activity)

    # A fresh client and different readings model redelivery after reload/history changes.
    result = await enrich_description(ActivityClient(api, "42"), "9", 80, 7, False)

    assert result["status"] == "unchanged"
    assert api.activity == edited
    assert api.activity["description"].count("AQI ") == 1
    assert api.activity["description"].count("UV Index ") == 1
    assert len(writes(api)) == 1


@pytest.mark.parametrize(("sport", "value"), [("Run", 3), ("TrailRun", 3), ("Ride", 12)])
async def test_workout_type_preview_write_verify_and_repeat(api, sport, value):
    api.activity.update(sport_type=sport, workout_type=0)
    client = ActivityClient(api, "42")
    assert (await client.update("9", {"workout_type": value}))["status"] == "would_update"
    assert not writes(api)
    assert (await client.update("9", {"workout_type": value}, False))["status"] == "updated"
    assert writes(api)[0][2]["data"] == {"workout_type": value}
    assert (await client.update("9", {"workout_type": value}, False))["status"] == "unchanged"
    assert len(writes(api)) == 1


@pytest.mark.parametrize("value", [True, "3", -1, 4, 13, None, 3.0])
async def test_workout_type_rejects_invalid_values_before_request(api, value):
    with pytest.raises(ActivityError, match="invalid_changes"):
        await ActivityClient(api, "42").update("9", {"workout_type": value}, False)
    assert not api.calls


@pytest.mark.parametrize(("sport", "value"), [("Ride", 3), ("Run", 12), ("Swim", 3), (None, 3)])
async def test_workout_type_rejects_wrong_sport(api, sport, value):
    api.activity["sport_type"] = sport
    with pytest.raises(ActivityError, match="invalid_workout_sport"):
        await ActivityClient(api, "42").update("9", {"workout_type": value}, False)
    assert not writes(api)


async def test_workout_type_ignored_by_provider_fails_verification(api):
    api.activity.update(sport_type="Run", workout_type=0)
    api.apply = False
    with pytest.raises(ActivityError, match="verification_failed"):
        await ActivityClient(api, "42").update("9", {"workout_type": 3}, False)


async def test_workout_type_rejects_combined_sport_change(api):
    api.activity["sport_type"] = "Run"
    with pytest.raises(ActivityError, match="invalid_changes"):
        await ActivityClient(api, "42").update(
            "9", {"sport_type": "Ride", "workout_type": 3}, False
        )
    assert not api.calls


async def test_sport_change_during_workout_tag_edit_prevents_write(api):
    api.activity.update(sport_type="Run", workout_type=0)
    original = api.async_request
    gets = 0

    async def racing(method, url, **kwargs):
        nonlocal gets
        if method == "GET":
            gets += 1
            if gets == 2:
                api.activity["sport_type"] = "Ride"
        return await original(method, url, **kwargs)

    api.async_request = racing
    with pytest.raises(ActivityError, match="activity_changed_retry"):
        await ActivityClient(api, "42").update("9", {"workout_type": 3}, False)
    assert not writes(api)


async def test_shoe_catalog_is_owned_filtered_and_read_only(api):
    api.activity = {
        "id": 42,
        "shoes": [
            {"id": "g123", "name": "Track shoe", "primary": False},
            {"id": "g456", "name": "Daily shoe", "primary": True},
            {"id": "b123", "name": "Bike"},
            {"id": "g789", "name": "Old shoe", "retired": True},
        ],
    }
    shoes = await ActivityClient(api, "42").shoes()
    assert shoes == [
        {"id": "g123", "name": "Track shoe", "primary": False},
        {"id": "g456", "name": "Daily shoe", "primary": True},
    ]
    assert api.calls[0][0:2] == ("GET", "https://www.strava.com/api/v3/athlete")
    assert not writes(api)


async def test_shoe_catalog_rejects_wrong_athlete(api):
    api.activity = {"id": 99, "shoes": [{"id": "g123", "name": "Someone else's shoes"}]}
    with pytest.raises(ActivityError, match="wrong_athlete"):
        await ActivityClient(api, "42").shoes()

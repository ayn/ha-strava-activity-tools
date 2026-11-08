"""Synthetic historical API contracts; no personal activity or coordinates."""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from aiohttp import web

from custom_components.strava_activity_tools.client import ActivityError
from custom_components.strava_activity_tools.environment import append_environment
from custom_components.strava_activity_tools.weather import (
    CREDIT,
    async_weather_readings,
    weather_readings,
    weather_request,
)

START = "2026-01-09T06:37:00Z"
HOUR = int(datetime(2026, 1, 9, 6, tzinfo=UTC).timestamp())


def payload():
    return {
        "utc_offset_seconds": 0,
        "hourly_units": {"time": "unixtime", "us_aqi": "USAQI", "uv_index": ""},
        "hourly": {"time": [HOUR], "us_aqi": [35.5], "uv_index": [0]},
    }


def test_query_uses_hour_at_run_start_and_rounded_coordinates():
    query = weather_request(START, [10.123456, 20.987654])
    assert query == {
        "latitude": 10.12,
        "longitude": 20.99,
        "hourly": "us_aqi,uv_index",
        "start_hour": "2026-01-09T06:00",
        "end_hour": "2026-01-09T06:00",
        "timezone": "UTC",
        "timeformat": "unixtime",
    }


def test_parse_valid_us_aqi_and_actual_uv_zero():
    assert weather_readings(payload(), START) == [35.5, 0]


@pytest.mark.parametrize("gps", [[91, 0], [0, 181], [True, 0], [0], [float("nan"), 0], None])
def test_invalid_location_never_forms_a_query(gps):
    with pytest.raises(ActivityError, match="weather_unavailable"):
        weather_request(START, gps)


@pytest.mark.parametrize(
    "field,value", [("us_aqi", None), ("uv_index", None), ("uv_index", True), ("us_aqi", -1)]
)
def test_missing_or_invalid_reading_does_not_become_zero(field, value):
    data = payload()
    data["hourly"][field] = [value]
    readings = weather_readings(data, START)
    assert readings[0 if field == "us_aqi" else 1] is None


@pytest.mark.parametrize("change", ["units", "time", "shape", "offset"])
def test_wrong_units_or_time_cannot_silently_match(change):
    data = payload()
    if change == "units":
        data["hourly_units"]["us_aqi"] = "European AQI"
    if change == "time":
        data["hourly"]["time"] = [HOUR + 3600]
    if change == "shape":
        data["hourly"]["uv_index"] = []
    if change == "offset":
        data["utc_offset_seconds"] = 3600
    with pytest.raises(ActivityError, match="weather_unavailable"):
        weather_readings(data, START)


@pytest.mark.usefixtures("socket_enabled")
@pytest.mark.parametrize("status", [200, 400, 429, 503])
async def test_actual_http_transport_has_only_location_time_and_no_auth(
    hass, aiohttp_server, status
):
    async def handle(request):
        assert "Authorization" not in request.headers
        assert dict(request.query) == {
            key: str(value) for key, value in weather_request(START, [10.123456, 20.987654]).items()
        }
        return web.json_response(payload(), status=status, headers={"Retry-After": "120"})

    app = web.Application()
    app.router.add_get("/weather", handle)
    server = await aiohttp_server(app)
    with patch(
        "custom_components.strava_activity_tools.weather.ENDPOINT", str(server.make_url("/weather"))
    ):
        if status == 200:
            assert await async_weather_readings(hass, START, [10.123456, 20.987654]) == [35.5, 0]
        else:
            with pytest.raises(ActivityError, match="weather_unavailable") as error:
                await async_weather_readings(hass, START, [10.123456, 20.987654])
            assert error.value.retry_after == (120 if status == 429 else 0)


def test_credit_only_added_when_api_data_adds_a_line_and_is_not_duplicated():
    original = "personal notes"
    first = append_environment(original, 35.5, 0, CREDIT)
    assert first.startswith(original + "\n\nAQI 36 💚\nUV Index 0 🟢\n")
    assert first.endswith(CREDIT)
    assert append_environment(first, 80, 5, CREDIT) == first
    local = append_environment(original, 35.5, 0)
    assert CREDIT not in append_environment(local, None, None, CREDIT)


def test_existing_credit_preserved_when_filling_a_second_metric():
    original = "AQI 36 💚\n" + CREDIT
    result = append_environment(original, None, 1, CREDIT)
    assert result.startswith(original)
    assert result.count(CREDIT) == 1
    assert result.endswith("UV Index 1 🟢")


LEGACY_CREDIT = (
    "Estimates: Open-Meteo / CAMS (https://open-meteo.com/en/docs/air-quality-api; CC BY 4.0)"
)
SHORT_CREDIT = "Open-Meteo/CAMS · https://open-meteo.com/en/licence"


def test_generated_credit_uses_short_license_link():
    assert CREDIT == SHORT_CREDIT


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_credit_only_migration_preserves_notes_metrics_and_newline_style(newline):
    original = newline.join(
        ["Coach's café notes  ", "", "AQI 36 💚", "UV Index 0 🟢", LEGACY_CREDIT, "tail  ", ""]
    )
    expected = original.replace(LEGACY_CREDIT, SHORT_CREDIT)
    assert append_environment(original, None, None) == expected
    assert append_environment(expected, None, None) == expected


@pytest.mark.parametrize(
    "credits",
    [
        [LEGACY_CREDIT, LEGACY_CREDIT],
        [LEGACY_CREDIT, SHORT_CREDIT],
        [SHORT_CREDIT, LEGACY_CREDIT, SHORT_CREDIT],
    ],
)
def test_only_exact_generated_credit_lines_are_normalized_and_deduplicated(credits):
    original = "AQI 36 💚\nUV Index 0 🟢\n" + "\n".join(credits) + "\nMore notes"
    expected = "AQI 36 💚\nUV Index 0 🟢\n" + SHORT_CREDIT + "\nMore notes"
    assert append_environment(original, None, None, CREDIT) == expected


@pytest.mark.parametrize(
    "credit_note",
    [
        "Quoted: " + LEGACY_CREDIT,
        LEGACY_CREDIT + " (my own note)",
        " " + LEGACY_CREDIT,
        LEGACY_CREDIT + " ",
        "My source is Open-Meteo / CAMS",
    ],
)
def test_credit_like_user_notes_are_never_rewritten(credit_note):
    original = "AQI 36 💚\nUV Index 0 🟢\n" + credit_note
    assert append_environment(original, None, None, CREDIT) == original


def test_filling_missing_metric_uses_existing_crlf_style_and_one_new_credit():
    original = "Notes\r\n\r\nAQI 36 💚\r\n" + LEGACY_CREDIT
    expected = "Notes\r\n\r\nAQI 36 💚\r\n" + SHORT_CREDIT + "\r\n\r\nUV Index 1 🟢"
    assert append_environment(original, None, 1, CREDIT) == expected

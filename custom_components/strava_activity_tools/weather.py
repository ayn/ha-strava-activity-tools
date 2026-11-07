"""Historical location-specific environmental estimates, without Strava credentials."""

from datetime import UTC
from typing import Any

from aiohttp import ClientError, ClientTimeout
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import ActivityError
from .core import nonnegative, parse_time

ENDPOINT = "https://air-quality-api.open-meteo.com/v1/air-quality"
TIMEOUT_SECONDS = 20
CREDIT = "Estimates: Open-Meteo / CAMS (https://open-meteo.com/en/docs/air-quality-api; CC BY 4.0)"


def weather_request(start: str, gps: list) -> dict:
    """Build a bounded, rounded-location query for the activity's UTC hour."""
    try:
        if not isinstance(gps, (list, tuple)) or len(gps) != 2:
            raise ValueError()
        if any(type(value) not in (int, float) for value in gps):
            raise ValueError()
        lat, lon = gps
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError()
        hour = parse_time(start).astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        return {
            "latitude": round(lat, 2),
            "longitude": round(lon, 2),
            "hourly": "us_aqi,uv_index",
            "start_hour": hour.strftime("%Y-%m-%dT%H:00"),
            "end_hour": hour.strftime("%Y-%m-%dT%H:00"),
            "timezone": "UTC",
            "timeformat": "unixtime",
        }
    except TypeError, ValueError:
        raise ActivityError("weather_unavailable") from None


def weather_readings(data: Any, start: str) -> list[float | None]:
    """Validate timestamps, units and numerical readings without inventing zeros."""
    try:
        hour = int(parse_time(start).timestamp()) // 3600 * 3600
        units, rows = data["hourly_units"], data["hourly"]
        if data["utc_offset_seconds"] != 0 or units != {
            "time": "unixtime",
            "us_aqi": "USAQI",
            "uv_index": "",
        }:
            raise ValueError()
        if rows["time"] != [hour] or len(rows["us_aqi"]) != 1 or len(rows["uv_index"]) != 1:
            raise ValueError()
        return [nonnegative(rows[name][0]) for name in ("us_aqi", "uv_index")]
    except KeyError, IndexError, TypeError, ValueError:
        raise ActivityError("weather_unavailable") from None


async def async_weather_readings(hass: Any, start: str, gps: list) -> list[float | None]:
    """Fetch only historical AQI/UV; no tokens, activity ID, title or route are sent."""
    params = weather_request(start, gps)
    try:
        async with async_get_clientsession(hass).get(
            ENDPOINT, params=params, timeout=ClientTimeout(total=TIMEOUT_SECONDS)
        ) as response:
            if response.status != 200:
                retry_after = 0
                if response.status == 429:
                    try:
                        retry_after = max(0, int(response.headers.get("Retry-After", "900")))
                    except ValueError:
                        retry_after = 900
                raise ActivityError("weather_unavailable", retry_after)
            return weather_readings(await response.json(), start)
    except ClientError, TimeoutError, ValueError:
        raise ActivityError("weather_unavailable") from None

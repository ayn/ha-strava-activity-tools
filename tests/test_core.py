"""Synthetic cases only; no real athlete activities or locations."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.strava_activity_tools.core import match_activity
from custom_components.strava_activity_tools.environment import (
    append_environment,
    environment_eligible,
    reading_at,
)

NOW = datetime(2026, 1, 10, tzinfo=UTC)


def run(**changes):
    return {
        "id": 9,
        "athlete": {"id": 42},
        "name": "Morning Run",
        "sport_type": "Run",
        "distance": 3200,
        "start_date": "2026-01-09T06:00:00Z",
        "start_latlng": [0.0, 0.0],
        "trainer": False,
        "manual": False,
        **changes,
    }


@pytest.mark.parametrize(
    ("activity", "filters", "expected"),
    [
        (run(name="My Tractive dog walk"), {"name_contains": "Tractive dog walk"}, True),
        (run(device_name="Fi Smart Collar"), {"device_exact": "Fi Smart Collar"}, True),
        (run(device_name="Fi Smart Collar 2"), {"device_exact": "Fi Smart Collar"}, False),
        (
            run(device_name="Peloton Bike", name="10 min Post-Run Stretch", sport_type="Workout"),
            {"device_contains": "Peloton", "name_contains": "post-run stretch"},
            True,
        ),
        (
            run(device_name="Peloton Bike", name="Core strength", sport_type="Workout"),
            {"device_contains": "Peloton", "name_contains": "post-run stretch"},
            False,
        ),
        (run(), {"sport_types": ["Run", "TrailRun"], "distance_below_miles": 2.1}, True),
        (run(distance=2.1 * 1609.344), {"distance_below_miles": 2.1}, False),
        (run(distance=3379.62), {"distance_below_miles": 2.1}, True),
        (run(distance=None), {"distance_below_miles": 2.1}, False),
        (run(distance=float("nan")), {"distance_below_miles": 2.1}, False),
        (run(sport_type="Ride"), {"sport_types": ["Run"]}, False),
        (run(), {"device_exact": "Fi Smart Collar"}, False),
        (run(), {}, True),
    ],
)
def test_match(activity, filters, expected):
    assert match_activity(activity, **filters) is expected


def test_environment_preserves_notes_and_is_idempotent():
    updated = append_environment("Coach notes\r\n", 50, 0)
    assert updated == "Coach notes\r\n\nAQI 50 💚\nUV Index 0 🟢"
    assert append_environment(updated, 90, 3) == updated


def test_append_only_missing_annotation():
    assert append_environment("AQI 70 💛", 30, 4) == "AQI 70 💛\n\nUV Index 4 🟡"


@pytest.mark.parametrize(
    ("aqi", "uv", "expected"),
    [
        (50, 2, "AQI 50 💚\nUV Index 2 🟢"),
        (51, 3, "AQI 51 💛\nUV Index 3 🟡"),
        (101, 6, "AQI 101 🧡\nUV Index 6 🟠"),
        (151, 8, "AQI 151 ❤️\nUV Index 8 🔴"),
        (201, 11, "AQI 201 💜\nUV Index 11 🟣"),
        (301, 0, "AQI 301 🤎 (Hazardous)\nUV Index 0 🟢"),
        (49.5, 2.5, "AQI 50 💚\nUV Index 3 🟡"),
    ],
)
def test_colors(aqi, uv, expected):
    assert append_environment(None, aqi, uv) == expected


@pytest.mark.parametrize("value", [None, "unknown", "unavailable", "", " ", -1, float("nan"), True])
def test_missing_history_not_zero(value):
    with pytest.raises(ValueError, match="history_unavailable"):
        append_environment("notes", value, 0)


def test_history_uses_last_valid_at_start_never_future():
    rows = [
        {"state": "30", "last_updated": "2026-01-09T05:00:00Z"},
        {"state": "80", "last_updated": "2026-01-09T06:01:00Z"},
    ]
    assert reading_at(rows, "2026-01-09T06:00:00Z") == 30
    rows.append({"state": "unavailable", "last_updated": "2026-01-09T05:50:00Z"})
    assert reading_at(rows, "2026-01-09T06:00:00Z") is None


def test_environment_eligibility():
    assert environment_eligible(run(), NOW, [0, 0])
    assert not environment_eligible(run(trainer=True), NOW, [0, 0])
    assert not environment_eligible(run(manual=True), NOW, [0, 0])
    assert not environment_eligible(run(sport_type="VirtualRun"), NOW, [0, 0])
    assert not environment_eligible(run(start_latlng=[1, 1]), NOW, [0, 0])
    assert not environment_eligible(
        run(start_date=(NOW - timedelta(days=8)).isoformat()), NOW, [0, 0]
    )
    assert not environment_eligible(
        run(start_date=(NOW + timedelta(days=1)).isoformat()), NOW, [0, 0]
    )
    assert not environment_eligible(run(start_latlng=None), NOW, [0, 0])

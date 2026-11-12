"""Keep synthetic short workouts in the feed; recheck changes before a mute write."""

from unittest.mock import AsyncMock, patch

import pytest

from custom_components.strava_activity_tools.client import ActivityClient, ActivityError
from custom_components.strava_activity_tools.core import match_activity
from custom_components.strava_activity_tools.muting import mute_activity, mute_parameters


def activity(**changes):
    return dict(
        id=9,
        athlete={"id": 42},
        sport_type="Run",
        distance=1600,
        workout_type=0,
        hide_from_home=False,
        **changes,
    )


@pytest.mark.parametrize(
    ("sport", "marker", "match"),
    [
        ("Run", 3, False),
        ("TrailRun", 3, False),
        ("VirtualRun", 3, False),
        ("Ride", 12, False),
        ("Run", 0, True),
        ("Run", None, True),
        ("Run", 2, True),
    ],
)
def test_exclude_tagged_workouts(sport, marker, match):
    row = activity()
    row.update(sport_type=sport, workout_type=marker)
    assert match_activity(row, distance_below_miles=2.1, exclude_workouts=True) is match


def test_workout_exemption_is_opt_in():
    row = activity()
    row["workout_type"] = 3
    assert match_activity(row, distance_below_miles=2.1)


@pytest.mark.parametrize("value", [None, 1, 0, "false", [], {}])
def test_workout_filter_requires_real_boolean(value):
    with pytest.raises(ActivityError, match="invalid_filters"):
        mute_parameters(dict(filters=dict(distance_below_miles=2.1, exclude_workouts=value)))


async def test_workout_tag_added_before_write_prevents_put():
    client = ActivityClient(AsyncMock(), "42")
    before = activity()
    after = dict(before, workout_type=3)
    with (
        patch.object(client, "get", AsyncMock(side_effect=[before, after])),
        patch.object(client, "_request", AsyncMock()) as request,
    ):
        result = await mute_activity(
            client,
            "9",
            dict(filters=dict(distance_below_miles=2.1, exclude_workouts=True), dry_run=False),
        )
    assert result["status"] == "skipped_ineligible"
    request.assert_not_called()

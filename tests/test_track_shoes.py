"""Track shoe suggestions are configuration-driven and never write."""

import math

from custom_components.strava_activity_tools.workouts import suggest_track_shoes

SHOES = [
    {"id": "g1", "name": "Track pair", "primary": False},
    {"id": "g2", "name": "Daily pair", "primary": True},
    {"id": "g3", "name": "Another pair", "primary": False},
]


def encoded(points):
    previous = [0, 0]
    out = []
    for point in points:
        for axis, coordinate in enumerate(point):
            value = round(coordinate * 1e5)
            delta = value - previous[axis]
            previous[axis] = value
            delta = ~(delta << 1) if delta < 0 else delta << 1
            while delta >= 32:
                out.append(chr((32 | (delta & 31)) + 63))
                delta >>= 5
            out.append(chr(delta + 63))
    return "".join(out)


def route():
    return encoded(
        [
            (0.00077 * math.cos(i * math.pi / 30), 0.00036 * math.sin(i * math.pi / 30))
            for i in range(601)
        ]
    )


def sample():
    run = {
        "sport_type": "Run",
        "name": "Track intervals",
        "workout_type": 3,
        "start_date": "2026-01-09T06:00:00Z",
        "timezone": "UTC",
        "gear_id": "g2",
        "laps": [{"distance": 400, "moving_time": 80 if i % 2 == 0 else 140} for i in range(8)],
    }
    run["map"] = {"polyline": route()}
    events = [{"start": "2026-01-09", "end": "2026-01-10", "summary": "4 x 400m"}]
    return run, events


def test_track_pair_comes_from_setting_and_replacement_is_immediate():
    run, events = sample()
    assert suggest_track_shoes(run, events, "UTC", "g1", SHOES)["changes"] == {"gear_id": "g1"}
    assert suggest_track_shoes(run, events, "UTC", "g3", SHOES)["changes"] == {"gear_id": "g3"}
    assert suggest_track_shoes(run, events, "UTC", "disabled", SHOES)["changes"] == {}


def test_explicit_other_shoe_is_preserved():
    run, events = sample()
    run["gear_id"] = "g3"
    assert suggest_track_shoes(run, events, "UTC", "g1", SHOES)["reason"] == "existing_shoe"


def test_existing_track_pair_is_unchanged():
    run, events = sample()
    run["gear_id"] = "g1"
    assert suggest_track_shoes(run, events, "UTC", "g1", SHOES)["status"] == "unchanged"


def test_intervals_without_track_evidence_do_not_assign_track_shoes():
    run, events = sample()
    run["name"] = "Road intervals"
    run["map"] = {"polyline": encoded([(i / 10000, 0) for i in range(100)])}
    assert suggest_track_shoes(run, events, "UTC", "g1", SHOES)["reason"] == "track_not_confirmed"


def test_calendar_track_label_requires_matching_session():
    run, events = sample()
    run["name"] = "Morning Run"
    events[0]["summary"] = "Track: 4 x 400m"
    assert suggest_track_shoes(run, events, "UTC", "g1", SHOES)["changes"] == {"gear_id": "g1"}
    run["laps"] = []
    run["workout_type"] = None
    assert suggest_track_shoes(run, events, "UTC", "g1", SHOES)["changes"] == {}


def test_unavailable_shoe_fails_closed():
    run, events = sample()
    assert suggest_track_shoes(run, events, "UTC", "g99", SHOES)["reason"] == "shoe_unavailable"


def test_race_keeps_its_own_shoes():
    run, events = sample()
    run["workout_type"] = 1
    assert suggest_track_shoes(run, events, "UTC", "g1", SHOES)["changes"] == {}


def test_treadmill_can_be_a_workout_but_never_uses_track_shoes():
    from custom_components.strava_activity_tools.workouts import suggest_workout

    run, events = sample()
    run.update(sport_type="VirtualRun", trainer=True, workout_type=None)
    events[0]["summary"] = "Track: 4 x 400m"
    assert suggest_workout(run, events, "UTC")["changes"] == {"workout_type": 3}
    result = suggest_track_shoes(run, events, "UTC", "g1", SHOES)
    assert result["changes"] == {}
    assert result["reason"] == "indoor_or_non_running"


def test_trainer_flag_on_regular_run_still_blocks_track_shoes():
    run, events = sample()
    run["trainer"] = True
    assert suggest_track_shoes(run, events, "UTC", "g1", SHOES)["changes"] == {}


def test_gps_track_can_be_identified_without_track_in_title():
    run, events = sample()
    run["name"] = "Evening Run"
    assert suggest_track_shoes(run, events, "UTC", "g1", SHOES)["changes"] == {"gear_id": "g1"}


def test_track_in_title_does_not_override_missing_gps_evidence():
    run, events = sample()
    run.pop("map")
    assert suggest_track_shoes(run, events, "UTC", "g1", SHOES)["reason"] == "track_not_confirmed"


def test_straight_road_repetitions_do_not_look_like_track():
    run, events = sample()
    run["map"] = {"polyline": encoded([(i / 100000, 0) for i in list(range(100)) * 8])}
    assert suggest_track_shoes(run, events, "UTC", "g1", SHOES)["changes"] == {}


def test_bad_or_oversized_gps_data_fails_closed():
    from custom_components.strava_activity_tools.tracks import decode_route, gps_track_pattern

    for value in (None, "?", "!", "_" * 100001, "~~~~~~~?", "zzzzzzzzzzzzzzzzzz"):
        assert decode_route(value) == []
    assert not gps_track_pattern({"map": None})
    assert not gps_track_pattern({"map": {"polyline": encoded([(0, 0)] * 50)}})


def test_single_track_loop_is_not_enough_gps_evidence():
    from custom_components.strava_activity_tools.tracks import gps_track_pattern

    run, _ = sample()
    run["map"] = {
        "polyline": encoded(
            [
                (0.00077 * math.cos(i * math.pi / 30), 0.00036 * math.sin(i * math.pi / 30))
                for i in range(61)
            ]
        )
    }
    assert not gps_track_pattern(run)


def test_200_meter_track_is_detected_but_easy_run_does_not_change_shoes():
    from custom_components.strava_activity_tools.tracks import gps_track_pattern

    run, events = sample()
    run["map"] = {
        "polyline": encoded(
            [
                (0.000385 * math.cos(i * math.pi / 30), 0.00018 * math.sin(i * math.pi / 30))
                for i in range(601)
            ]
        )
    }
    assert gps_track_pattern(run)
    run.update(workout_type=None, laps=[{"distance": 1000, "moving_time": 500}])
    events[0]["summary"] = "15 min easy"
    assert suggest_track_shoes(run, events, "UTC", "g1", SHOES)["changes"] == {}


def test_short_road_approach_does_not_hide_repeated_200m_track_running():
    from custom_components.strava_activity_tools.tracks import gps_track_pattern

    run, _ = sample()
    oval = [
        (0.000385 * math.cos(i * math.pi / 30), 0.00018 * math.sin(i * math.pi / 30))
        for i in range(601)
    ]
    approach = [(0.002 - i * 0.00002, 0) for i in range(80)]
    run["map"] = {"polyline": encoded(approach + oval + list(reversed(approach)))}
    assert gps_track_pattern(run)


def test_partial_track_jog_has_track_evidence_but_no_workout_or_shoe_change():
    from custom_components.strava_activity_tools.tracks import track_evidence

    run, events = sample()
    oval = [
        (0.000385 * math.cos(i * math.pi / 30), 0.00018 * math.sin(i * math.pi / 30))
        for i in range(241)
    ]
    approach = [(0.003 - i * 0.00002, 0) for i in range(130)]
    run["map"] = {"polyline": encoded(approach + oval + list(reversed(approach)))}
    run.update(workout_type=None, laps=[])
    events[0]["summary"] = "15 min easy"
    assert track_evidence(run)["on_track"] is True
    assert suggest_track_shoes(run, events, "UTC", "g1", SHOES)["changes"] == {}

"""Synthetic prescription/activity matching; no personal calendar fixtures."""

from copy import deepcopy

import pytest

from custom_components.strava_activity_tools.workouts import calendar_window, suggest_workout


def activity():
    return {
        "sport_type": "Run",
        "workout_type": None,
        "start_date": "2026-01-08T23:00:00Z",
        "timezone": "(GMT+08:00) Asia/Taipei",
        "distance": 7200,
        "laps": [{"distance": 400, "moving_time": 86 if i % 2 == 0 else 150} for i in range(16)],
    }


def event(summary="Run: 8 x 400m at 10k effort / 400m easy", **extra):
    return {"summary": summary, "start": "2026-01-09", "end": "2026-01-10", **extra}


def test_intervals_require_prescription_and_recorded_fast_easy_laps():
    result = suggest_workout(activity(), [event()], "UTC")
    assert result["status"] == "suggested"
    assert result["changes"] == {"workout_type": 3}
    assert result["reason"] == "matched_interval_laps"
    assert result["matched_reps"] == 8


@pytest.mark.parametrize("label", [1, 2, 3])
def test_manual_race_longrun_or_workout_label_is_preserved(label):
    run = activity()
    run["workout_type"] = label
    assert suggest_workout(run, [event()], "UTC")["reason"] == "existing_label"


@pytest.mark.parametrize("title", ["8 miles easy", "Rest day", "Run - Race: Half Marathon"])
def test_easy_rest_or_race_is_not_tagged_as_workout(title):
    assert suggest_workout(activity(), [event(title)], "UTC")["changes"] == {}


def test_uniform_autolaps_are_not_interval_evidence():
    run = activity()
    for lap in run["laps"]:
        lap["moving_time"] = 100
    assert suggest_workout(run, [event()], "UTC")["reason"] == "insufficient_lap_evidence"


def test_separate_warmup_does_not_match_the_quality_session():
    run = activity()
    run["laps"] = [{"distance": 1609, "moving_time": 600}]
    assert suggest_workout(run, [event()], "UTC")["changes"] == {}


def test_multiple_matching_calendar_entries_abstain():
    assert suggest_workout(activity(), [event(), event()], "UTC")["reason"] == "ambiguous_calendar"


def test_timed_intervals_and_timezone_day_boundary():
    run = activity()
    run["laps"] = [{"distance": 300 if i % 2 == 0 else 180, "moving_time": 60} for i in range(10)]
    assert suggest_workout(run, [event("5 x 1 min hard / 1 min easy")], "UTC")["changes"]
    previous_day = event(start="2026-01-08", end="2026-01-09")
    assert suggest_workout(run, [previous_day], "UTC")["reason"] == "no_calendar_match"


def test_multiple_sets_match_in_order_with_recovery_laps():
    run = activity()
    run["laps"] = [{"distance": d, "moving_time": t} for d, t in [(1000, 230), (300, 105)]] * 3 + [
        {"distance": d, "moving_time": t} for d, t in [(400, 86), (250, 90)]
    ] * 8
    assert suggest_workout(run, [event("3 x 1k; 8 x 400m")], "UTC")["matched_reps"] == 11


@pytest.mark.parametrize("mutation", ["no_laps", "cancelled", "different_sport", "bad_time"])
def test_missing_or_ineligible_evidence_abstains(mutation):
    run, prescribed = activity(), event()
    if mutation == "no_laps":
        run.pop("laps")
    elif mutation == "cancelled":
        prescribed["status"] = "cancelled"
    elif mutation == "different_sport":
        run["sport_type"] = "Ride"
    else:
        run["start_date"] = "2026-01-09"
    assert suggest_workout(run, [prescribed], "UTC")["changes"] == {}


def test_dst_calendar_window_uses_local_midnights():
    run = activity()
    run.update(start_date="2026-03-08T15:00:00Z", timezone="America/New_York")
    start, end = calendar_window(run, "UTC")
    assert start.isoformat() == "2026-03-08T00:00:00-05:00"
    assert end.isoformat() == "2026-03-09T00:00:00-04:00"


def test_suggestion_does_not_mutate_inputs_or_return_prescription_text():
    run, events = activity(), [event()]
    original = deepcopy((run, events))
    result = suggest_workout(run, events, "UTC")
    assert (run, events) == original
    assert "summary" not in result
    assert "description" not in result


def test_standing_recoveries_use_elapsed_time_and_zero_distance():
    run = activity()
    for i in range(1, len(run["laps"]), 2):
        run["laps"][i] = {"distance": 0, "moving_time": 0, "elapsed_time": 90}
    assert suggest_workout(run, [event()], "UTC")["changes"] == {"workout_type": 3}


def test_race_pace_instruction_is_not_a_race_entry():
    assert suggest_workout(activity(), [event("8 x 400m at race pace")], "UTC")["changes"]


@pytest.mark.parametrize("timezone", ["Unknown/Zone", 42])
def test_bad_timezone_abstains(timezone):
    run = activity()
    run["timezone"] = timezone
    assert suggest_workout(run, [event()], "UTC")["reason"] == "invalid_activity_time"


def test_partial_match_must_not_ignore_an_unsupported_nested_set():
    run = activity()
    run["laps"] = [{"distance": 160 if i % 2 == 0 else 80, "moving_time": 30} for i in range(8)]
    result = suggest_workout(run, [event("8x (1:15 hard; 45s moderate) + 4x30s strides")], "UTC")
    assert result["changes"] == {}

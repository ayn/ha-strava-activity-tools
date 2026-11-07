"""Release detection must not bless unknown releases or accept unsafe action outputs."""

from copy import deepcopy

import pytest

from scripts.provider_release import choose_target, load_config


def test_pinned_baseline_is_an_immutable_commit():
    target = choose_target(load_config(), "baseline", None)
    assert target["repository"] == "ayn/ha_strava"
    assert len(target["ref"]) == 40
    assert target["known_unsupported"] == "false"


@pytest.mark.parametrize(
    "tag,sha,blocked",
    [
        ("4.6.7", "d5fcffe56555c537ae63a48e422ceb3045cb5b8c", "true"),
        ("4.6.8", "a" * 40, "false"),
        ("4.6.7", "a" * 40, "false"),
    ],
)
def test_only_exact_known_unsupported_tag_and_commit_are_classified(tag, sha, blocked):
    calls = []

    def fetch(path):
        calls.append(path)
        return (
            {"tag_name": tag, "draft": False, "prerelease": False}
            if path.endswith("/latest")
            else {"sha": sha}
        )

    target = choose_target(load_config(), "latest-release", fetch)
    assert target["ref"] == sha
    assert target["label"] == tag
    assert target["known_unsupported"] == blocked
    assert len(calls) == 2


@pytest.mark.parametrize("tag", ["tag\nref=attacker", "$(oops)", "", "x" * 129])
def test_release_names_cannot_inject_action_outputs(tag):
    with pytest.raises(ValueError):
        choose_target(load_config(), "latest-release", lambda _: {"tag_name": tag})


def test_latest_release_does_not_accept_a_draft_or_prerelease():
    for field in ("draft", "prerelease"):
        with pytest.raises(ValueError):
            choose_target(
                load_config(), "latest-release", lambda _: {"tag_name": "4.6.8", field: True}
            )


def test_upstream_main_resolves_to_a_commit_not_a_moving_ref():
    target = choose_target(load_config(), "upstream-main", lambda _: {"sha": "b" * 40})
    assert target["repository"] == "craibo/ha_strava"
    assert target["ref"] == "b" * 40


def test_unexpected_provider_repository_is_rejected():
    config = deepcopy(load_config())
    config["baseline"]["repository"] = "somewhere/unrelated"
    with pytest.raises(ValueError):
        choose_target(config, "baseline", None)

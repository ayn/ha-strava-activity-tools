# Contributing

Use a feature branch and pull request. Behavioral changes need a failing
regression before the implementation, focused tests proving the intended behavior
afterward, and the full suite passing. The same requirement applies to maintainer,
agent and outside contributions, including fixes submitted to other projects.

Use synthetic fixtures only. Do not include real athletes, accounts, routes,
training prescriptions, tokens or deployment configuration. Native HA lifecycle,
config-flow and HTTP tests should exercise the real framework where mocks could
hide registration, routing, cleanup or callback failures.

For affected paths, test account isolation, untrusted input shapes and IDs,
missing permissions, duplicate deliveries, fresh reads before writes, independent
edits, partial failures, timeouts, retry persistence and restart/unload behavior.
Negative cases must produce no sensitive events or unintended writes. Advance
HA's test clock for scheduled retries instead of waiting on wall-clock sleeps.

Before pushing:

```sh
ruff check .
ruff format --check .
python -m pytest --cov=custom_components.strava_activity_tools --cov=custom_components.ha_strava_automation --cov-report=term-missing --cov-fail-under=90
```

Review the complete diff and affected call path. State what changed, how it was
tested and any limits in the PR description. Skipped opt-in provider contracts
are not passing contracts. Do not lower coverage or silently exempt an upstream
release to clear CI. Never test by injecting events into a live write automation.

Documentation-only edits do not require behavioral tests, version bumps or
releases. Code changes and releases do not authorize installation in anyone's
Home Assistant instance. Keep the unreleased standalone work in `development/`
until its packaging and cutover are ready.

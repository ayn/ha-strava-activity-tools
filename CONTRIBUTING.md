# Contributing

Open an issue for a substantial feature before implementing it. Keep changes
focused and include a failing regression test for behavioral fixes.

Use Python 3.14 and the dependencies in requirements-dev.txt. Run pytest with
coverage, ruff check, and ruff format --check. The CI workflow runs the same
checks and skips documentation-only changes.

Use synthetic fixtures. Never check in real activity exports, routes, calendar
feeds, credentials, deployment files or user accounts. Preserve preview defaults,
OAuth ownership, narrow writes and idempotence. Document provider compatibility
exceptions and test failure/retry behavior.

Code is MIT licensed. Open-Meteo/CAMS data attribution and provider API terms are
separate requirements; do not remove source credit from API-derived annotations.

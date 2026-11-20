# Contributor and agent guide

- Use a feature branch and pull request; never push directly to main.
- Behavioral changes need red/green regressions, the full suite and required CI
  checks. Apply the same standard to outside contributions and third-party PRs
  we prepare. Exercise real HA HTTP/config-flow/lifecycle boundaries where mocks
  would hide failures. Never weaken assertions or skip failures to get green CI.
- Keep fixtures synthetic: no production activities, athletes, routes, locations,
  training prescriptions, tokens or deployment configuration.
- Run pytest with at least 90% coverage, Ruff check and Ruff format checks before
  pushing. Documentation-only edits do not need artificial tests, CI/release
  triggers or version bumps. Never publish just to clear an update prompt.
- Own OAuth through native HA Application Credentials and OAuth2Session. Keep one
  refresh owner per account. Do not copy secrets through tools, shell or files.
  Author auth and auth tests from official HA/Strava sources without consulting
  another provider's auth implementation/tests. Do not claim formal clean-room
  provenance: prior architecture reading is disclosed in docs/PROVENANCE.md.
- No scheduled activity discovery/polling. Timers only retry saved work or prune
  retained state. Share subscriptions and API budgets per client ID, with separate
  account queues. Do not delete another account's or integration's subscription.
- Unsigned webhook fields, subscription IDs and routing IDs are not authentication.
  Verify an activity's owner using the selected account's authenticated API data
  before events, disclosures or mutations. Fail closed on ambiguous identity.
- Preview calls never write or enqueue future writes. Live operations persist
  intent, reread remote state, preserve independent edits and verify narrow writes.
  Never log raw provider bodies, credentials or credential-bearing URLs.
- Keep portable operations separate from HA adapters. Prefer native automations
  and reusable blueprints over a separate rule builder. Track detection is a
  heuristic; shoes require both track and workout evidence, never an easy track jog.
- workout_type is a deliberate undocumented-update-field compatibility exception:
  preserve strict integer/sport validation and read-back checks. Feed mute does not
  change activity privacy.
- Publishing code does not authorize a live installation or activity mutation.
  Read docs/AGENT_SETUP.md before using HA MCP. Use native HA/store/config-flow APIs;
  do not edit .storage or ask for secrets in chat. Never fire synthetic events into
  live write automations. Verify a version actually loaded after downloading.

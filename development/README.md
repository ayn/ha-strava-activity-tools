# Strava Automation: unreleased independent integration

This is unreleased development code, outside the released companion's
`custom_components/strava_activity_tools` installation path. Packaging, live
callback validation and an explicit deployment are required before cutover.
The released companion is unchanged by this staging directory.

The new domain is `ha_strava_automation`. Its own Strava API app and native HA
Application Credentials/config entry own OAuth. It does not depend on or import
`ha_strava`. Both integrations can coexist using separate API app/client IDs;
Strava permits one webhook subscription per API app. A supported, confirmed
connection handover can reuse an existing app after stopping its old refresh owner.

Implemented and tested:

- Native OAuth authorization-code flow with HA's signed state/callback handling.
- Athlete identity verified using authenticated `GET /api/v3/athlete`, strict IDs,
  duplicate prevention and preview enabled for new accounts.
- Explicit requested scopes, stored grants from the token exchange, optional
  write/profile consent and rejection of missing activity-read consent.
- Native refresh locking/persistence, rotation validation, sanitized failures,
  reauthentication and preservation of existing entry settings.
- Runtime setup, identity verification, unload/reload and transient setup retry.
- Native webhook routing with bounded validation and durable acceptance before ACK.
- Account-local inboxes and durable operations with verified remote ownership,
  read-before-write checks, persistent retries and app-wide quota accounting.
- Native actions for matching, feed muting, historical AQI/UV, calendar/lap workout
  classification and track shoes, with preview enabled by default.
- Setup readiness gating, shared-app subscription lifecycle, account options,
  bounded display history and guarded connection migration.

The current documented token-exchange response contains `scope`; this prototype
requires it for initial consent verification. HA's standard callback does not
forward the scope query parameter. Missing/malformed token grants fail closed.
Real Strava login, iOS/Safari callback behavior and production installation still
need validation before release. The new integration does not reproduce the other
provider's activity/photo sensors or automatically migrate their consumers.

## Development and tests

Use the repository's pinned development environment. Tests temporarily mount
only this development component into the test process's `custom_components`
namespace. There is no production loader or install hook.

```sh
python -m pytest tests/standalone -q
ruff check .
ruff format --check .
```

Fixtures are synthetic. Tests exercise native HA config flows, Application
Credentials, signed HTTP callbacks, OAuth session refresh and entry lifecycle.
HTTP boundary mocks keep ordinary cases offline; rejection tests also use a
loopback HTTP server. They do not perform real provider logins or activity writes.

The published custom-component test harness currently pins HA 2026.10.4. Trying
it against HA 2026.11.2 fails during framework fixture imports, before collection.
The pinned baseline is the reproducible suite; current-version live verification
is a separate check and must not be described as a passing current-version suite.

For an advanced agent-assisted Activity Tools handover, pause its automations,
allow active jobs to finish and disable/unload the old companion. After the native
connection migration, keep the new entry in preview and call the admin action
`ha_strava_automation.adopt_activity_tools_state` with the new account entry and
the stopped companion entry IDs. It validates the athlete and original source,
preserves completed receipts, and never enables writes or changes the old store.
Update all event/action consumers and verify receipts before enabling automation.
There is no separate Activity Tools migration wizard.

## Implementation references and provenance

The auth implementation and tests were newly authored from these primary sources
and the installed Home Assistant framework APIs:

- [Strava authentication](https://developers.strava.com/docs/authentication/)
- [Strava webhooks](https://developers.strava.com/docs/webhooks/)
- [HA configuration flows](https://contributors.home-assistant.io/core/integrations/config-flow/)
- [HA native OAuth2 framework](https://github.com/home-assistant/core/blob/2026.10.4/homeassistant/helpers/config_entry_oauth2_flow.py)
- [HA Application Credentials](https://github.com/home-assistant/core/tree/2026.10.4/homeassistant/components/application_credentials)

The authoring agent had previously read the existing provider's architecture
guide while planning. Its auth implementation and tests were not consulted for
this code, and none were copied. This is not a claim of formal clean-room
development. Existing companion code has its own history and is outside this
auth-specific provenance statement. Existing repository licenses still apply.

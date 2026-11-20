# Strava Automation: independent authentication prototype

This is unreleased development code, outside the released companion's
`custom_components/strava_activity_tools` installation path. It is not a working
automation replacement yet. No new release, repository rename or live cutover is
part of this authentication milestone.

The new domain is `ha_strava_automation`. Its own Strava API app and native HA
Application Credentials/config entry own OAuth. It does not depend on or import
`ha_strava`. Both integrations can coexist using separate API app/client IDs;
Strava permits one webhook subscription per API app. Webhook registration and
activity actions have **not** been implemented here.

Implemented and tested:

- Native OAuth authorization-code flow with HA's signed state/callback handling.
- Athlete identity verified using authenticated `GET /api/v3/athlete`, strict IDs,
  duplicate prevention and preview enabled for new accounts.
- Explicit requested scopes, stored grants from the token exchange, optional
  write/profile consent and rejection of missing activity-read consent.
- Native refresh locking/persistence, rotation validation, sanitized failures,
  reauthentication and preservation of existing entry settings.
- Runtime setup, identity verification, unload/reload and transient setup retry.

The current documented token-exchange response contains `scope`; this prototype
requires it for initial consent verification. HA's standard callback does not
forward the scope query parameter. Missing/malformed token grants fail closed.
Real Strava login, iOS/Safari callback behavior and production installation still
need validation before release. No claim of full provider or automation parity.

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

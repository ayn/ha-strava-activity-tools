# Strava Automation

<img src="custom_components/ha_strava_automation/brand/icon.png" width="72" height="72" alt="Strava Automation icon">

An independent, MIT-licensed Home Assistant integration for Strava automations.
Process new uploads through webhooks, preview changes, and retry saved work after
connection failures. No activity polling or separate server is needed.

[![Open in HACS or Marketplace](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ayn&repository=ha-strava-automation&category=integration)
[![Set up Strava Automation](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=ha_strava_automation)

This repository was formerly **Strava Activity Tools**. Strava Automation manages
its own Strava authorization and webhook. Install it as a **custom repository**
in Marketplace or HACS.

## Webhook access required

Strava must be able to reach the integration's webhook over **public HTTPS**.
Strava sends a GET request to verify the webhook during setup and POST requests
to deliver activity events. Pick one way to make that endpoint reachable:

- **Home Assistant Link (formerly Home Assistant Cloud).** Sign in to your
  Nabu Casa account so the integration can use a cloud webhook without port
  forwarding.
- **Public tunnel or reverse proxy.** Use a public HTTPS endpoint such as
  Tailscale Funnel or Cloudflare Tunnel and configure its URL as Home Assistant's
  external URL.
- **Your own public HTTPS URL.** Forward a port to Home Assistant or an HTTPS
  reverse proxy and configure Home Assistant's external URL. Use dynamic DNS
  if your public IP address changes.

Use a trusted TLS certificate. Keep the generated `/api/webhook/…` path reachable
for both methods without a browser login or challenge. A LAN address, private
Tailscale access, or Tailscale Serve alone cannot receive Strava's requests.
The `my.home-assistant.io` OAuth redirect only handles sign-in. Event delivery
uses the separate webhook URL.

## What it does

| Feature | Behavior |
| --- | --- |
| Historical AQI and UV | Append worldwide US EPA AQI and UV from the outdoor run's start location/hour, using Open-Meteo/CAMS estimates; optional HA sensor history |
| Feed muting | Match title, device, sport and distance; keep tagged workouts visible |
| Gear assignment | Apply shoes or a bike using native automations and selected filters |
| Workout classification | Match recorded interval laps against a selected HA training calendar |
| Track shoes | Require both workout evidence and GPS track geometry; preserve indoor and independently selected shoes |
| Previews and history | Separate Latest preview / Latest run entities with bounded, sanitized local history |
| Durable processing | Persist webhook acceptance and operation intents, retry transient failures and suppress completed duplicate intents |

Four importable [blueprints](blueprints/automation) provide muting, gear,
AQI/UV and workout classification. They run through HA's automation engine;
there is no second rule builder. Filters combine with AND; use multiple rules
for OR alternatives. Feed muting does not change activity privacy.

Examples include Tractive/Fi dog walks, Peloton stretches, short warm-up/cooldown
runs with a Workout-tag exemption, and shoes reserved for track workouts.
Track detection is a bounded GPS geometry heuristic, not Garmin track-mode
metadata. Easy track laps alone do not justify workout shoes.

## Install and connect

1. Use HA **2026.10.4 or newer**. Add this repository as a custom **Integration**
   repository in Marketplace or HACS, download a versioned release and restart HA.
2. Create a personal [Strava API app](https://www.strava.com/settings/api).
   Set its Authorization Callback Domain to `my.home-assistant.io` for HA's
   standard OAuth redirect. Follow Strava's current eligibility/API policies.
3. In **Settings → Devices & services → Add integration → Strava Automation**,
   enter the app's Client ID and Secret using native HA Application Credentials,
   then approve access in Strava. Do not paste tokens into YAML or automations.
4. Import a blueprint and select this account. Configure **Track shoes** in the
   integration's options if wanted. Review results using its history entities or
   the native [history card example](examples/history-card.yaml).
5. New accounts and blueprints start in **preview**. To allow writes, disable
   Preview only in the integration **and** explicitly disable the automation's
   preview/dry-run setting after checking its suggestions.

Activity read consent is required. `activity:write` enables mutations;
`profile:read_all` enables the shoe catalog. Declined optional permissions keep
those capabilities unavailable. Reauthorization preserves the account's options.
Each account has its own queue and identity checks. Accounts sharing one app
share its remote subscription and quota budget.

To keep another Strava integration installed alongside this one, give each a
**different API app/client ID**: Strava permits one webhook subscription per app.
To move an existing connection and reuse its API app, see the
[connection migration guide](docs/MIGRATION.md).

## Native actions and events

Actions use `ha_strava_automation.*` and an explicit `config_entry_id`:

- `get_activity`, `match_activity`, `detect_track`, `suggest_workout`: read-only.
- `update_activity`, `mute_activity`, `append_environment`, `process_workout`:
  preview by default; live operation intents and retries survive restart.
- `list_jobs`, `replay_job`: inspect processing or explicitly replay a failed job.

Actions require HA administrator access; native automations can call them.
`ha_strava_automation_activity_ready` fires only after authenticated API ownership
verification. `ha_strava_automation_workout_processed` follows verified live
classification; use it for short-run muting to avoid racing workout tags.
Error/recovery events contain IDs and safe categories, never credentials/routes.
Use the event account ID to keep multi-account automations isolated.

`workout_type` editing is a deliberate compatibility exception to the published
Strava update schema. Strict sport/value validation and read-back checks apply;
it is not a guarantee of future provider support. Calendar matching currently
focuses on explicit interval sets. Ambiguous sessions, races, uniform auto-laps
and missing evidence abstain. Continuous tempo/progression matching and assigning
one prescription across doubles remain planned.

## Reliability and privacy

Webhook input is untrusted. A matching subscription or owner ID never authorizes
a write: the integration fetches the activity with the selected athlete's OAuth
session, verifies ownership, and rechecks remote state before narrow mutations.
It preserves independent notes and guarded fields, verifies writes, and treats
already-applied updates as no-ops. Completed intents are retained for seven days.

Transient errors retry existing work using one-shot timers. API budgets are
shared per app and persisted, including rate-limit waits. Permanent authorization
failures need reconnection/replay; they do not loop forever. Persistent HA
notifications surface failures. Storage failures stop processing instead of
resetting receipts. Queue acceptance is durable before the webhook ACK; Strava
redelivery remains necessary for deliveries missed while HA is unavailable.
Ready-event delivery can repeat after a crash; consumers must be idempotent.

OAuth tokens stay in native HA private storage. Private job stores retain only
necessary IDs/settings; history excludes titles, descriptions and routes.
Weather lookups send rounded start coordinates and UTC hours to Open-Meteo and
retain attribution. No AI service is used. HA recorder, traces and backups have
separate retention: exclude activity events from recorder if unwanted, review
trace settings, and protect HA access and backups. All repository fixtures are
synthetic, with no production accounts, activity payloads or credentials.

[Agent setup](docs/AGENT_SETUP.md) · [Migration](docs/MIGRATION.md) ·
[Contributing and tests](CONTRIBUTING.md) · [Validation](docs/VALIDATION.md) ·
[Research](docs/RESEARCH.md) · [Planned features](docs/PLAN.md) ·
[Feature tracker](https://github.com/ayn/ha-strava-automation/issues/5)

## Development

```sh
uv venv --python 3.14
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest --cov=custom_components.ha_strava_automation --cov-report=term-missing --cov-fail-under=90
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

The reproducible harness currently pins HA 2026.10.4. Current-version deployment
checks are separate from that test baseline; see [validation](docs/VALIDATION.md).
Auth and its tests were written from official framework and protocol references.
See [provenance](docs/PROVENANCE.md).
Existing competitor/provider API policies apply separately from this MIT license.

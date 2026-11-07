# Strava Activity Tools

<img src="custom_components/strava_activity_tools/brand/icon.png" width="72" height="72" alt="Strava Activity Tools icon">

Experimental, MIT-licensed Home Assistant companion for `ha_strava`.

[![Open in HACS or Marketplace](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ayn&repository=ha-strava-activity-tools&category=integration)
[![Set up Strava Activity Tools](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=strava_activity_tools)

**Early access:** requires `ha_strava` with activity-created events from
[upstream #349](https://github.com/craibo/ha_strava/pull/349). The catalog submission
and general onboarding wait for a provider release containing that feature.
These buttons support custom-repository installation in the meantime.

- Add historical US EPA AQI and UV at an outdoor run’s start location worldwide,
  or use optional HA sensor history.
- Build native automations for feed mute, gear assignment and description edits.
- Preview calendar-assisted workout tags and GPS-based track-shoe suggestions.
- Process uploads through the existing Strava webhook and OAuth connection, with
  durable AQI/UV retries and duplicate protection. No activity polling.

Preview is enabled by default. Suggestion actions are read-only; an explicitly
enabled native automation can apply their proposed tags and shoes through the
update action with the returned state checks.
This is an early community integration, not a default Marketplace listing.

[Research](docs/RESEARCH.md) · [Roadmap](docs/PLAN.md) ·
[Feature tracker](https://github.com/ayn/ha-strava-activity-tools/issues/5) ·
[TDD evidence and validation](docs/VALIDATION.md) · [Publishing readiness](docs/PUBLISHING.md)

## Available features

Three blueprints run through HA's native automation engine:

| Blueprint | Conditions / action | Install |
| --- | --- | --- |
| [Mute activity](blueprints/automation/mute_activity.yaml) | Title/device exact or contains, sports, distance; hide from home feed | [Import](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fayn%2Fha-strava-activity-tools%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fmute_activity.yaml) |
| [Assign gear](blueprints/automation/assign_gear.yaml) | Same filters; assign shoes or bike, or clear gear | [Import](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fayn%2Fha-strava-activity-tools%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fassign_gear.yaml) |
| [Append environment](blueprints/automation/append_environment.yaml) | Outdoor GPS run worldwide; append historical AQI and UV | [Import](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fayn%2Fha-strava-activity-tools%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fappend_environment.yaml) |

**Preview is enabled by default**, independently for every automation. An additional
integration-wide **Preview only** option defaults to enabled and prevents writes
even when an action requests them. Both gates must allow writes before a mutation
is possible. Check preview results before considering a cutover. Mute uses `hide_from_home`;
it does not make an activity private, remove it, or exclude it from statistics.

Filters combine with AND. Use multiple automations for OR alternatives.
The mute blueprint requires at least one filter; empty filters do not mute everything.
Title/device contains is case insensitive; exact matching is case sensitive.
A missing device or distance does not match a filter requiring that field.

Example mute configurations, all without private athlete data:

| Rule | Blueprint inputs |
| --- | --- |
| Tractive walks | Title contains `My Tractive dog walk` |
| Fi walks | Device exactly `Fi Smart Collar` |
| Peloton pre-run stretch | Device contains `Peloton`; title contains `pre-run stretch` |
| Peloton post-run stretch | Device contains `Peloton`; title contains `post-run stretch` |
| Short warm-up/cooldown runs | Sports `Run`, `TrailRun`; distance below **2.1 miles** |

The short-run cutoff is strict: a run exactly 2.1 miles is not muted. This is a
heuristic, not a determination of workout role. Review class title wording and
existing rules before migration. Do not mute every Peloton `Workout`: that also
includes strength and other sessions.

## Connectivity before installation

`ha_strava` needs a configured external HA URL. Strava’s servers must be able to
reach its `/api/strava/webhook` callback for validation and activity notifications.
The provider registers that callback; this companion reuses its events and OAuth
session. Marketplace/HACS installation and blueprint imports do not configure
network access or Strava authorization for you.

Use a publicly reachable HTTPS hostname through one of these options:

- **Home Assistant Link** (formerly Home Assistant Cloud/Nabu Casa remote access),
  with remote access enabled.
- A public tunnel or reverse proxy, such as Cloudflare Tunnel or Tailscale Funnel.
- Port forwarding with a public connection, DNS/dynamic DNS and TLS.

Ordinary Tailscale/Serve or another private VPN is insufficient: Strava cannot
join your private network. Funnel exposes a public URL. The callback must accept
Strava’s validation GET and event POST without an interactive login or browser
challenge in front of it. A proxy’s interactive access policy cannot block this
path. A dynamic DNS name alone does not make a callback reachable through CGNAT.

Set the Internet URL in **Settings → System → Network → Home Assistant URL**,
or select the cloud URL where available. The **OAuth authorization callback
domain** in the Strava API app is separately set to `my.home-assistant.io`, as
required by `ha_strava`; do not confuse it with the webhook hostname.

See [HA remote access](https://www.home-assistant.io/docs/configuration/remote/),
[Strava webhooks](https://developers.strava.com/docs/webhooks/),
[Home Assistant Link](https://contributors.home-assistant.io/architecture/platform/cloud/),
and [Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel).

## Installation and prerequisites

- HA **2026.10.4**, the tested baseline; Python 3.14 for development.
- A configured, loaded `ha_strava` account with a reachable callback as described above.
- Strava app Client ID/Secret for the provider’s OAuth setup. Strava currently
  requires a subscription to create a personal API app; see its
  [getting-started guide](https://developers.strava.com/docs/getting-started/).
  No second Strava app, login or webhook is needed for this companion.
- The activity-created event from upstream [PR #349](https://github.com/craibo/ha_strava/pull/349),
  or a version containing that feature. This dependency is not yet assumed merged.
- Existing Strava OAuth consent with `activity:write` for actual writes;
  `activity:read_all` when processing private activities. Reauthorize through the
  OAuth owner if scopes are missing. This companion does not grant missing scopes.
- HA recorder history only if selecting HA sensor history. Location API mode needs no HA weather sensors or home zone.

Use the **Open in HACS or Marketplace** button above, or add
`https://github.com/ayn/ha-strava-activity-tools` as a custom Integration repository.
The same repository and releases serve both stores. The older `hacs_repository`
link opens HACS before HA 2026.11 and Marketplace starting with that version.
Download the integration; restart when requested by the store. This does not imply
listing or approval in the default catalog. The runtime test baseline remains
HA 2026.10.4; a 2026.11 runtime test is still pending.

For a manual installation, copy `custom_components/strava_activity_tools` into
HA’s `custom_components` folder and restart. Add **Strava Activity Tools** in
Settings → Devices & Services, select the loaded Strava entry, and enter the
numeric athlete ID. Never copy an OAuth refresh token into this integration.

Use an **Import** link in the blueprint table, or copy desired files from
`blueprints/automation/` to `blueprints/automation/strava_activity_tools/`.
Create an automation from the Blueprints page and start in preview mode.
Blueprint imports are separate from integration installation. AQI/UV,
muting and generic gear assignment have importable blueprints today. A complete
[workout-tag/track-shoe blueprint](https://github.com/ayn/ha-strava-activity-tools/issues/11)
is planned; currently those services need an explicitly configured native automation. Existing users of the earlier
`activity_tools` prototype should follow [the migration guide](docs/MIGRATION.md).

Using an HA MCP agent? Ask it to follow the
[agent-assisted setup guide](https://github.com/ayn/ha-strava-activity-tools/blob/main/docs/AGENT_SETUP.md).
It covers prerequisite checks, multiple accounts, preview-first rules, the native
history dashboard, verification and rollback. Repository coding agents should
also read [AGENTS.md](AGENTS.md); HA MCP does not load that file automatically.

Do not enable this enrichment alongside another writer doing the same job until
a dry run confirms the intended handoff. Existing cloud workers and rules need
not be removed for local development.

## Previews and processing history (0.6.2)

Each account has **Latest preview** and **Latest run** sensors under
**Settings → Devices & services → Strava Activity Tools → Devices**. They update
from actions and webhook processing, without activity polling. Select either
sensor to see the latest result and its `recent` list, or use **Add to dashboard**
for a native tile. This is available in v0.6.2 and newer, not v0.6.1.

For a readable list with activity links, proposed/completed changes, AQI/UV,
matching reasons and retry/error details, add a native **Manual** dashboard card
using [examples/history-card.yaml](examples/history-card.yaml). It discovers the
integration's history entities, so no account or entity IDs need editing. No
custom frontend card is required. Multiple accounts are shown separately.

Preview and processing streams each retain up to 25 operation results for seven
days, persist across normal restarts, and are deleted when the account is removed.
This is operation history: several rules can produce several entries for one
Strava activity. A rule match or workout suggestion does not mean a change was
written. Preview results, actual writes, no-ops, ineligible skips and retries
have distinct statuses. Read-only matching and track detection appear in previews
even when writes are enabled. Ordinary `get_activity` reads are not recorded.

History starts with this version; old job receipts are not backfilled. It stores
selected result metadata, not activity titles, descriptions, GPS routes or tokens.
Title/description edits are shown as `changed`; gear uses the Strava gear ID.
Like other HA entity states, these results are visible to users who can access
those entities. The sliding list is excluded from Recorder to avoid duplicating
the full list on every update; individual entity states follow your Recorder
retention. Display history is separate from durable job receipts and never
replays work. Dashboard failures do not authorize retries of successful writes.

## Actions and events

Actions accept the **Strava Activity Tools configuration entry ID**, not the source
Strava entry ID, plus an activity ID. Full responses remain available in automation
traces; the history entities expose selected summaries.

- `strava_activity_tools.get_activity`: fetch selected condition fields by ID.
- `strava_activity_tools.match_activity`: evaluate filters without writing.
- `strava_activity_tools.suggest_workout`: preview calendar/lap matching and track-shoe suggestions.
- `strava_activity_tools.detect_track`: inspect GPS track evidence without writing.
- `strava_activity_tools.update_activity`: preview or update explicit documented fields.
- `strava_activity_tools.append_environment`: preview, or durably enqueue a historical append.
- `strava_activity_tools.list_jobs`: inspect jobs and retry/error status without an API request.
- `strava_activity_tools.replay_job`: replay a failed, unexpired job after fixing its cause.

`suggest_workout` also returns an `expected` snapshot of the label, gear, sport,
and indoor/manual flags. An explicitly enabled native automation can pass its
proposed tag/shoe changes and that snapshot to `update_activity` with `dry_run: false`.
The update aborts if those fields changed after the preview, including just before
the write; this preserves intervening manual choices. Suggestions themselves remain
read-only. Strava does not offer an atomic conditional write, so a final network-race
window remains. These generic updates are not durable queued jobs.

Update fields: `name`, `description`, `sport_type`, `gear_id`, `hide_from_home`,
`trainer`, `commute`. Unsupported/private API fields are rejected. Gear IDs use
`b…` for bikes and `g…` for shoes; `none` clears gear. The track-shoe picker is
available in Configure; a [generic shoe/bike picker](https://github.com/ayn/ha-strava-activity-tools/issues/13) is planned.

The bridge consumes `ha_strava_activity_created` and emits
`strava_activity_tools_activity_ready` with account/activity IDs, title, device, sport,
distance in meters/miles, start date, and trainer/manual flags. It does not emit
routes, photos, descriptions, or tokens. `strava_activity_tools_error` contains a stable
error category and IDs for repair, not the raw API response.

## AQI/UV behavior

- Use historical data at the activity's timezone-aware UTC start, up to seven
  days after the activity. Delayed uploads do not use current conditions.
- Require an outdoor, non-manual `Run`/`TrailRun` with valid GPS.
- Location-based mode (`weather_source: location_api`) gets historical hourly
  US EPA AQI and cloudy-sky UV from Open-Meteo/CAMS at the run's start location.
  Coverage is global, including Taiwan; US EPA names the index scale, not the
  coverage area. No HA sensors, home zone or recorder are required.
- New action calls without sensor settings, and new blueprint instances without
  selected sensors, use location-based mode. An explicit location mode uses the
  API at home as well as while traveling.
- HA sensor history is optional (`weather_source: ha_history`). In that mode,
  prefer local history within the home radius, with optional `external_fallback`
  for away runs or missing readings. Select a **US EPA AQI index** sensor and a
  UV index sensor; units are not guessed or converted from PM2.5 or another index.
  This mode reads the Recorder snapshot at the activity's start. Existing
  sensor-based calls retain their behavior and original saved-job identities.
- Unknown/missing readings never become zero. Actual UV zero is valid.
- Append missing lines only; preserve existing notes and existing AQI/UV lines.
- Use familiar AQI/UV color bands, with an explicit hazardous label above AQI 300.
- The API uses the UTC hour containing the run start, never today's current values
  or a daily UV maximum. Estimates can differ from monitoring stations; CAMS global
  spatial resolution is roughly 45 km, versus roughly 11 km in Europe.
- Only rounded start coordinates (two decimals) and the selected hour are sent.
  No Strava credentials, activity ID, title or route leave HA for the weather API.
  API-derived additions include an Open-Meteo/CAMS credit. Data uses
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); see the
  [provider documentation](https://open-meteo.com/en/docs/air-quality-api).
  Personal non-commercial access needs no API key; commercial use requires a
  [provider plan](https://open-meteo.com/en/pricing).
- API failures enter the existing durable retry flow. They do not block unrelated
  Strava fetch jobs. Fully annotated activities make no external weather request.
- No external weather service is required when using only local HA history.
  No AI or Cloudflare Worker is needed for either path.

## Reliability and privacy

There is no scheduled Strava polling, second webhook subscription, token store,
or persistent activity database. API requests use the existing OAuth session.
Edits are serialized per athlete, owner checked, re-read immediately before a
write, and verified afterward. This narrows the edit race; Strava provides no
atomic conditional write here, so a last-moment external edit remains possible.

Jobs and completed receipts now survive HA restarts through the native private,
atomic `Store` helper. Only IDs, account scope, sensor/settings references, timing,
attempts, and safe error categories are persisted. No activity bodies or credentials
are stored in the queue. Records expire after seven days and are bounded to 1,024
per account. Changed parameters create a separate intent; previews never enqueue writes.

For actual AQI/UV writes, the action returns `job_id` and a status such as `queued`,
`done`, `failed`, or `skipped`. Use **Developer Tools → Actions → Strava Activity Tools:
List jobs** to inspect the selected account, and **Replay job** with its job ID
when a failed job's cause has been fixed. Replays retain the original account,
settings and expiration deadline. Queued, running, completed, and expired jobs
cannot be manually replayed as failed work.

Five attempts use 1-minute, 5-minute, 30-minute and 2-hour backoffs. Rate-limit
Retry-After also delays other jobs in the same worker and survives a restart.
One-shot timers process only existing jobs or retention cleanup; there is no
scheduled activity discovery or API polling. Writes always re-fetch remote state,
so an append that took effect before a timeout is not blindly repeated.

Permanent failures notify immediately; transient incidents notify after the third
failure. HA persistent notifications deduplicate by account/job and are restored
on reload. Successful verification clears the incident and emits
`strava_activity_tools_recovered`. Expired or ineligible work clears the stale warning
without claiming verified recovery. Optional external notification automations
can consume the events; no email/mobile destination is enabled by this project.
Storage failure stops the worker and prompts inspection/reload, rather than
resetting receipts. Account changes and malformed saved jobs fail closed.

These guarantees start after job intent is committed. Webhooks missed while HA
is down, and a crash before an internal event reaches the durable queue, are not
recovered by polling. Delivery of a ready event is at least once across an
interrupted attempt; downstream automations must remain idempotent. The generic
mute/gear/update actions are not automatically turned into durable jobs by this
release; only event preparation and AQI/UV processing use the queue.

HA recorder and automation traces are separate persistence systems. Exclude
`strava_activity_tools_activity_ready` and `strava_activity_tools_error` from recorded events,
and review trace/backup retention and HA access before public use. An all-day
training calendar alone cannot safely identify a completed activity as warm-up.

No production data, credentials, real activity fixtures, or location exports are
included. Examples and tests are synthetic. No data is sent to an AI service.
Provider API policies apply separately from this project’s code license.

## Calendar-assisted workout suggestions

`strava_activity_tools.suggest_workout` is a read-only action. Select the Strava Activity Tools
account, activity ID, and training-calendar entity. It fetches the owned activity
and that local day's calendar entries through HA's native `calendar.get_events`.
It returns `status`, a stable `reason`, and proposed `changes`, never a write.

The initial matcher recognizes explicit distance/time interval repeats and
multiple sets. Recorded lap lengths/times must match the prescribed work reps,
with slower adjacent recovery laps. Standing recoveries use elapsed time.
Uniform auto-laps, missing evidence, unsupported nested repeats, ambiguous
matches, easy/rest/race entries, and existing non-default labels abstain. Local
dates use the activity's IANA timezone, including daylight saving boundaries.
This is an initial interval matcher, not a general workout parser: continuous
threshold/tempo/progression and inferred coach units still need further work.
A matching date alone does not establish that the prescribed session was done.
A match does not reserve a prescription for one upload: multiple activities can
match the same calendar entry. [Doubles/session assignment](https://github.com/ayn/ha-strava-activity-tools/issues/17)
is planned; account for this limitation when enabling automatic writes.

The generic `update_activity` action also accepts integer `workout_type`: run
codes 0–3 and cycling codes 10–12, with sport validation and post-write verification.
This is an explicit compatibility exception supported by Strautomator's public
implementation but omitted from the current Strava update schema. Provider
compatibility has been tested against a local HTTP server, not established by a
new live tag mutation. Changing sport and subtype together is rejected. The
preview lock and action-level dry-run default still apply. The integration does
not apply calendar suggestions automatically: the user must explicitly configure
a native automation to pass its proposed changes and `expected` snapshot to the
write action.

## Track shoes and reusable track detection

In **Settings → Devices & services → Strava Activity Tools → Configure**, choose
**Track shoes** from this athlete's active Strava shoes, or Disabled. This stores
a gear ID in private HA options and shows its name. Replacements take effect on
the next preview without editing automations. Opening Configure refreshes the
catalog using the existing OAuth session; no new token store or polling is added.
If profile permission is missing, **Request Strava reauthorization** starts the
existing source integration's native approval flow. Complete that flow in HA and
Strava, then reopen Configure. Missing catalog access does not block the preview
lock or reset a saved pair. Reauthorization does not add another Strava entry.

`strava_activity_tools.detect_track` returns read-only `on_track`, `track_fraction`, and
the compatibility alias `likely_track`, using
GPS geometry, independently of shoe settings or calendar access. It decodes a
bounded precision-5 route, checks for repeated compact oval running at a typical
200–400 m track scale, and rejects straight shuttles, short routes, malformed input,
indoor/virtual/manual activities and missing GPS. It needs no predefined location
list, external geocoder, Garmin account or AI. Garmin track-mode metadata is not
reliably available through the documented Strava activity response.

This is a heuristic, not proof of a track: an oval park circuit can resemble one,
and noisy GPS or a combined road warm-up can cause abstention. A bounded segment scan tolerates
a road approach when track-like segments cover at least 20% of the route; an easy run
that merely includes some track laps still does not establish a track workout. Only full detailed
polylines are used; no attempt is made to infer a complete route from a simplified
summary. Computation runs in HA's executor rather than its main event loop. Input
is capped at 100,000 encoded characters / 10,000 points, with no route persistence
or route logging by this component.

Calendar-preview responses distinguish `is_workout` from `on_track` and include a
separate `track_shoes` suggestion. A likely
outdoor track plus a recognized interval session or an existing Workout label can
suggest the selected shoes. Treadmill workouts can still receive a Workout-tag
suggestion but never a track-shoe suggestion. A different non-primary shoe is
preserved; an already selected track shoe needs no change. Strava cannot tell us
whether a default shoe was explicitly selected by the user, so that distinction
must be considered before enabling automatic writes. Unavailable catalog data
abstains for shoes independently of workout tagging. **Shoe and tag suggestions
are read-only.** An explicit native automation can apply qualifying suggestions
using `update_activity` and the `expected` snapshot. The snapshot checks preserve
intervening changes to sport, label, gear and indoor/manual flags. The generic
update action has no durable retry queue: native delays/repeats are automation
configuration and pending attempts do not survive a Core restart.

## Planned features

These are proposals, not available features or delivery commitments. See the
[pinned roadmap](https://github.com/ayn/ha-strava-activity-tools/issues/5) for
priorities, acceptance criteria and discussion.

- [Durable mute/tag/gear jobs](https://github.com/ayn/ha-strava-activity-tools/issues/7),
  plus [guided setup](https://github.com/ayn/ha-strava-activity-tools/issues/9) and
  [processing health, previews and repair](https://github.com/ayn/ha-strava-activity-tools/issues/10).
- [Importable workout/track-shoe blueprints](https://github.com/ayn/ha-strava-activity-tools/issues/11),
  [rule exceptions and metric units](https://github.com/ayn/ha-strava-activity-tools/issues/12),
  and [friendly shoe/bike pickers](https://github.com/ayn/ha-strava-activity-tools/issues/13).
- [Use the matched workout title](https://github.com/ayn/ha-strava-activity-tools/issues/24):
  `Morning Run` → `3 × 20 × 400`, with opt-in renaming and manual-title protection.
- [Title/description templates](https://github.com/ayn/ha-strava-activity-tools/issues/14)
  and [optional historical weather fields and formatting](https://github.com/ayn/ha-strava-activity-tools/issues/15).
- [Continuous quality sessions](https://github.com/ayn/ha-strava-activity-tools/issues/16),
  [doubles and warm-up/cooldown context](https://github.com/ayn/ha-strava-activity-tools/issues/17),
  and [cycling workout matching](https://github.com/ayn/ha-strava-activity-tools/issues/20).
- [Gear mileage and maintenance reminders](https://github.com/ayn/ha-strava-activity-tools/issues/18).
- [Optional Workoutbridge/Prescribed.fit integration](https://github.com/ayn/ha-strava-activity-tools/issues/22)
  to match completed activities against structured plans. The design would support
  self-hosted and hosted sources, with no hosted account required by this component.

## Development

```sh
uv venv --python 3.14
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest --cov=custom_components.strava_activity_tools --cov-report=term-missing
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

Behavioral tests cover real HA config flows, services, event dispatch, recorder
SQL history, and blueprint execution, with fake OAuth responses. Live deployment
and public release validation are deliberately separate from these tests.

License: MIT. Code is original to this prototype; no competitor implementation was copied.

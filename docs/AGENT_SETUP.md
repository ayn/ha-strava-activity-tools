# Agent-assisted setup

Use this checklist when someone asks an HA MCP agent to install or configure
Strava Activity Tools. Prefer discovery and the user's existing setup over
asking for values the connected HA instance already knows. The README documents
supported features and version requirements; [PUBLISHING.md](PUBLISHING.md)
tracks release/catalog gates. This guide does not imply default catalog listing
or that every upstream provider release contains the required event.

## 1. Discover before changing anything

- Read the HA MCP best-practices guide and the references matching the work.
  Use `ha_get_integration` to discover loaded `ha_strava` and
  `strava_activity_tools` entries, including disabled entries and options.
- Use `ha_get_hacs_info` to inspect the installed and available versions. Check
  the Core version: the tested baseline is HA 2026.10.4. The store is HACS on that
  version and Marketplace on 2026.11+. Use the store actually present.
- Establish which Strava account the user wants, which rules they want, and
  whether this is a fresh preview-only setup or an upgrade to existing live rules.
- On upgrades, inspect existing automations, options and `list_jobs` results.
  Keep a private comparison snapshot of receipt IDs/statuses and settings. Avoid
  printing activity responses or credentials. Existing source OAuth and job
  receipts must survive installation. Do not remove/recreate entries to upgrade.

Tool names here describe HA MCP capabilities; if a server version uses different
names, discover the equivalent native API rather than inventing a service.

## 2. Resolve the provider prerequisite

If no loaded `ha_strava` entry exists, stop the companion config flow and explain:
“Install and configure ha_strava first; this companion reuses that connection.”
Link the user to [ha_strava](https://github.com/craibo/ha_strava) and its setup
instructions. Downloading the companion does not automatically install or
configure the provider. Help with provider installation within the user's
authorized scope, then resume companion setup after it loads.

The provider owns the Strava app Client ID/Secret, OAuth consent and public
webhook endpoint. These are not a second set of companion credentials. Follow
the provider's current app instructions and
[Strava's app requirements](https://developers.strava.com/docs/getting-started/).
The companion needs `activity:write` for writes, `activity:read_all` for private
activities, and appropriate profile access for its shoe catalog. Use the
provider's reauthorization flow if permissions are missing. The user completes
interactive Strava consent; never ask them to paste refresh tokens into chat or
write tokens to this repository.

Verify the installed provider contains the activity-created event required by
[PR #349](https://github.com/craibo/ha_strava/pull/349), or a released equivalent.
Do not infer support from the version being recent. Until an upstream release
contains it, explain the prerequisite and available development path explicitly;
never silently replace the provider with a fork or edit its installed files.

For webhook connectivity, use the README's connectivity checklist. Strava needs
public access to the provider's `/api/strava/webhook` callback for validation GETs
and event POSTs. Home Assistant Link remote access, a public tunnel/Funnel or
properly configured public HTTPS forwarding are possible approaches; private
Tailscale/Serve alone is insufficient. Do not bypass interactive security policies
or open router ports automatically. Browser OAuth success does not prove webhook
reachability. Distinguish the app's OAuth callback domain from the webhook hostname.

## 3. Install and connect the companion

1. Use the store's dedicated custom-repository/download tools, e.g.
   `ha_manage_hacs`, for `ayn/ha-strava-activity-tools`, category Integration.
   Select a compatible release. If the user knowingly chooses an unreleased
   build, record its exact commit/ref and name the previous rollback version.
   Store first-run notices and GitHub-account linking belong to the user.
2. Check configuration, explain any required brief restart, and load the new code
   as part of the authorized installation. If the user has said to defer restarts,
   keep that constraint. Reconnecting an old Python module does not necessarily
   load newly downloaded code; verify after restart.
3. Add **Strava Activity Tools** through the config flow, selecting the intended
   loaded source entry. The current flow also asks for a numeric athlete ID.
   Use the verified source identity where available, not a guessed profile ID.
   The flow rejects an ID belonging to a different source account.
4. Keep **Preview only** enabled on new entries. A development version string is
   not proof of a public release or a successful catalog submission.

For upgrades, preserve the existing `preview_only` and `track_shoes` options,
entry IDs, enabled automations and receipt store. Do not switch an established
live account to preview silently or enable writes on a new account silently.

Multiple athletes use separate provider entries and separate companion entries.
Each person authorizes their own connection, subject to Strava app athlete limits.
Jobs, options, shoes and history are scoped per companion account. Every automation
must select its intended companion entry ID, not the provider's entry ID. Filtering
the event's account is essential when more than one is loaded.

## 4. Configure native rules and previews

Import supported blueprints from the approved release/ref using the dedicated
blueprint tool (`ha_manage_blueprints`) or the README's import links. Create native
automations with the config API. Do not hand-edit `automations.yaml` or `.storage`.

Currently shipped blueprints cover mute, generic gear assignment and AQI/UV.
The complete workout-tag/track-shoe blueprint is still planned in #11; do not
pretend it is available. Those actions currently require an assembled native
automation with the documented guards.

Select account and filters deliberately. Distance filters are currently miles
and use a strict cutoff. Feed mute is not privacy. For AQI/UV, location API mode
needs no home weather sensors; HA history is an optional mode. Explain the external
provider and coordinate-sharing behavior documented in the README. Track shoes
are selected by name through Configure; generic gear assignment currently uses
a gear ID. Calendar suggestions abstain when evidence is insufficient.

Keep each new automation's preview enabled as well as the integration-wide lock.
Use an owned activity and an explicitly read-only action to verify behavior, such
as `detect_track`, `suggest_workout`, or an update/environment call with
`dry_run: true`. Do not trigger an existing live automation or fire a synthetic
provider/ready event as a supposedly harmless test: downstream rules may write.

## 5. Make results easy to find

On builds with history support, the integration creates **Latest preview** and
**Latest run** sensors per account. Discover their entity IDs through the registry
or `integration_entities('strava_activity_tools')`; never hard-code an athlete ID.
The entity's `stream` attribute identifies `previews` versus `runs`.

- Point the user to the integration's device page for the latest result and its
  `recent` attribute, or add native tiles to an agreed dashboard.
- If asked for a history dashboard, inspect existing dashboards first, then use
  `ha_config_set_dashboard` or the equivalent Lovelace API. Preserve unrelated
  views/cards. A new sidebar dashboard should use a path containing a hyphen.
- [examples/history-card.yaml](../examples/history-card.yaml) is a standard
  Markdown card. Supply its parsed configuration through the dashboard API; no
  custom frontend resource or extra HACS card is required. It discovers accounts
  automatically. Give status tiles and history lists a layout that fits mobile.
- Render the actual template against HA states and re-read the saved dashboard.
  Use a dashboard screenshot if already available; do not enable beta features
  just to claim visual verification.

History begins when a compatible build is installed. It does not backfill old
receipts. A preview, match or suggestion is not a completed write; one activity
may have several operation results. Limits are 25 results per stream and seven
days. Stored display metadata excludes raw titles, descriptions, routes and
tokens. Entity visibility follows HA access, not separate per-athlete login ACLs.

## 6. Verify and hand off

- Confirm source and companion entries are loaded, expected entities exist, and
  the chosen store ref matches the downloaded build.
- Check logs for integration errors. Compare options, enabled consumers and job
  receipt IDs/statuses against the pre-upgrade snapshot. A receipt changed by
  legitimate processing is different from a lost/reset store; investigate rather
  than blindly reverting live work.
- Confirm a read-only preview is visible. Empty processing history is expected
  until an actual action runs; do not fabricate a successful write to populate it.
- Confirm no duplicate worker/automation is performing the same change. Existing
  muting or description writers should be handed off deliberately, not disabled
  without understanding their rules.
- Enable writes only when the user asks to go live: both the integration lock
  and selected automation previews must permit writes. Preserve owner checks,
  expected-state guards, narrow field updates, and existing manual choices.
- Report a direct dashboard link, installed version/ref, preview/live status,
  validation performed, and any remaining user steps. Keep personal activity and
  account data out of public issues, examples, commits and screenshots.

For a code rollback, reinstall the recorded prior compatible version and restart
as needed while retaining config entries and receipts. Do not restore an old
receipt snapshot or remove the store: that could repeat completed actions. A code
rollback cannot undo Strava edits already made. Do not use a full HA restore to
undo this one integration update.

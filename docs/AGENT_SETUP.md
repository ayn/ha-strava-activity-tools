# Agent-assisted setup

Strava Automation is an independent integration, domain `ha_strava_automation`.
Use HA MCP's installed tools and best-practice guide. Installing this component
alone does not grant permission to enable writes or change real activities.

1. Discover existing integrations, accounts, application credentials, automations
   and dashboards. Ask only for details the connected instance cannot supply.
   Do not assume the first athlete is the intended one. Do not return tokens,
   app secrets, full webhook URLs or private activity bodies in tool output.
2. Check the HA version, Marketplace/HACS state and recovery backup. Use a
   versioned release/custom repository; complete store notices in the user's UI.
   Downloading does not prove the runtime loaded; restart when required.
3. For a new connection, use native Application Credentials and the integration's
   config flow. The user completes Strava browser consent. Verify the resulting
   account identity and scopes. Read consent is required; writes/profile are
   optional. Keep preview enabled until the user authorizes a live rule.
4. For another installed provider, choose a separate API app for coexistence, or
   follow [the supported connection handover](MIGRATION.md). Never share one app's
   callback across independent integrations or refresh the same token concurrently.
5. Check public HTTPS webhook access, including validation GET and event POST.
   Ordinary private VPN access is insufficient. Do not expose unrelated HA paths
   or weaken an entire reverse-proxy policy to allow one callback.
6. Import the selected blueprint and choose this account. Select a training calendar
   for workout classification and a shoe from its own gear catalog if requested.
   Keep per-rule dry_run and integration Preview only enabled for initial checks.
7. Preview a real owned activity without writing. Verify account isolation,
   expected changes, separate history streams and retained job receipts. Do not
   inject synthetic activity-ready events into a live automation as a test.
8. If authorized, enable the chosen rules and integration writes. Short-run muting
   should consume `ha_strava_automation_workout_processed` after classification,
   rather than racing `ha_strava_automation_activity_ready`. Other mute rules can
   process normal activity-ready events. Preserve unrelated automations.
9. Help the user see results using Latest preview / Latest run, the native example
   card, action responses, job status and notifications. No dashboard is installed
   automatically. Raw activity data is not appropriate for public screenshots.
10. Report installed version, restart result, account/rule state, receipt comparison
    and remaining user steps. Repository installation does not imply default-store
    listing. State test baseline and live validation separately.

## Advanced Activity Tools handover through MCP

There is no general Activity Tools migration wizard. If the owner requests this
handover, first stop its automation triggers with `stop_actions: false`, wait for
active actions/jobs to finish, then disable/unload its entry. Save the configurations
and receipt counts privately; never reset failed or completed receipts to simplify
migration. Use the native connection handover for the old OAuth provider.

With the new account loaded in preview, call the admin action
`ha_strava_automation.adopt_activity_tools_state` using its `config_entry_id` and
the stopped companion's `source_entry_id` (the companion entry ID, not its provider).
It verifies the athlete and original OAuth-source association, validates receipts,
keeps completed intents completed, retains the source store and preserves preview.
A running/ambiguous legacy receipt needs review before transfer. Repeating adoption
is idempotent and does not overwrite newly selected shoes. No credentials are copied
by this action. Display history is separate and is not transferred by this action.

Update event/action/blueprint references, external listeners and dashboard entities.
Compare the imported receipt IDs/statuses before enabling writes. Keep all activity
payloads and deployment notes private. Only remove old entries/files once remaining
consumers and cleanup effects have been checked. Follow the native APIs; do not edit
HA's internal storage to make an entry appear migrated.

### Marketplace domain rename

Marketplace 2026.11 blocks removing repository files while any old-domain config
entry exists, including disabled entries. A normal domain rename therefore needs
repository uninstall/reinstall. Do not bypass that guard or edit internal storage.

When all old jobs are terminal, an advanced agent can retain their receipt metadata
using List jobs and privately retain the configured operation parameters. Validate
each original job ID against the canonical intent hash before removing anything.
After the supported OAuth handover is active, `import_completed_jobs` in 0.7.1+
accepts only full, validated done/skipped receipt records for the expected athlete,
with a matching native migration proof and the target still in preview. It rejects
pending/running/failed records and mismatched intent hashes; it never contacts
Strava or enables writes. Records retain their original seven-day expiry.

This is an explicit MCP/admin handover, not an automatic migration wizard. Save
parameters/receipts only in private operational storage, never public source or
logs. Recheck the final snapshot after pausing rules. If pending or unmatched work
exists, finish or review it before removing the old entry. Preserve source settings,
restore the shoe through the new account's native picker, and verify receipt
counts/statuses before re-enabling rules. Private Store backups are a recovery
path, not permission to edit .storage directly.

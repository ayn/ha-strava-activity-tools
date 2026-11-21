# Connection migration from ha_strava

Version 0.7.0 replaces the companion architecture with independent Strava
Automation. It does not depend on pending provider PRs. A normal new installation
uses its own Strava API app and native HA authorization.

**Coexistence:** keep the other integration with a different app/client ID.
Strava supports one remote webhook subscription per app, so two independent
integrations cannot share that callback. Each integration also needs its own
refresh owner.

## Reuse an existing app and authorization

For an existing Activity Tools installation, Marketplace may require removing the
old config entry before uninstalling/reinstalling its renamed repository. Preserve
terminal receipts through [the advanced agent handover](AGENT_SETUP.md#marketplace-domain-rename)
first. Do not delete an entry containing pending/unreviewed work or bypass the
store guard. A new installation does not need this step.

1. Take a current HA backup and save affected automation/dashboard configurations.
   Pause dependent automations without cancelling active writes; let existing work
   finish. Disable/unload the old Activity Tools companion if present.
2. Keep the selected `ha_strava` connection loaded. Add **Strava Automation** and
   choose **Migrate from ha_strava**, then select that connection.
3. The preflight verifies a native HA OAuth session, authenticated athlete, grants,
   app credentials and existing callback. Callback identity uses saved connection
   metadata, or the documented exact legacy route registered on the configured
   HTTPS HA origin. A hostname match alone is insufficient. Shared or unverified apps/callbacks are
   refused before disabling the old provider. Unknown grants require fresh consent;
   they are never guessed from what a previous integration requested.
4. Confirm the handover. The flow imports native Application Credentials, records
   recoverable migration intent and stops the old refresh owner before creating
   the new connection. Tokens remain inside HA; no token export is necessary.
5. The new connection starts in preview. It may replace only the exact verified old
   callback after the old connection is stopped. Setup must fully succeed before
   saved live work can execute. A temporary registration failure retries setup.
6. Update consumers explicitly. Provider activity/photo sensors are not recreated.
   Existing custom automations/dashboard entities/listeners are not automatically
   rewritten. See [the agent guide](AGENT_SETUP.md) for a requested MCP handover
   of Activity Tools receipts/settings; there is no separate companion wizard.
7. Verify a read-only owned activity, account options, receipt counts, callback
   health and new rule references. Enable writes only when the user authorizes it
   and one writer owns the account. Remove old code/entries only after confirming
   their cleanup cannot revoke the migrated authorization or delete the new callback.

The disabled source remains initially for recovery. Do not blindly re-enable it
after the new owner refreshes its token: its saved refresh token may be stale.
Rollback must first stop the new owner and explicitly restore authorization and
callback ownership; otherwise reconnect the old provider with fresh consent.
A full HA restore affects unrelated changes and needs separate authorization.

If preflight cannot establish scopes/callback identity, it preserves the old
connection and reports why. Do not bypass that guard or invent permissions.
Use a separate app, or complete fresh consent, if a safe handover cannot be proven.

# Migrating from the local Activity Tools prototype

The public repository is `ayn/ha-strava-activity-tools`; its integration domain,
actions, and events use `strava_activity_tools`. No new Strava OAuth application
or webhook subscription is needed. The existing `ha_strava` entry still owns OAuth.

1. Install the new integration, then restart Home Assistant.
2. Save copies of the existing automation configurations and note their enabled states.
   Disable consumers of `activity_tools_activity_ready` and disable the old
   **Activity Tools** integration. Leave its files and config entry in place for rollback.
3. Add **Strava Activity Tools** with the same Strava source and athlete ID. Select
   the disabled legacy entry in the optional migration field.
4. Completed/skipped receipts are copied using HA's native Store API. The source store
   remains unchanged. Ownership, record integrity, and capacity are checked before saving;
   unfinished legacy jobs require review rather than automatic replay. Retention remains
   seven days. Setup defaults to preview-only and preserves the configured track shoes.
5. Replace action prefixes `activity_tools.` with `strava_activity_tools.`. Replace
   event prefixes `activity_tools_` with `strava_activity_tools_`, and update each consumer's
   `config_entry_id` to the new entry ID. Update recorder event exclusions if configured.
   Search automations, scripts, scenes, dashboards, and other consumers for stale references.
6. Check loaded state and receipts; test read-only actions. Restore the previous preview
   setting and enable only the migrated consumers. Keep any older external writer disabled.

If setup fails, keep the new entry and consumers disabled, restore the saved automation
configs, and re-enable the old entry. Never run both writers. New work performed after
cutover is not copied back to the legacy store; review those receipts before rolling back
following live writes. Do not edit `.storage` files directly.

A later HACS/Marketplace installation of this same repository retains the public domain
and config entry. This repository supports custom-repository installation; it is not yet
listed in a default store catalog.

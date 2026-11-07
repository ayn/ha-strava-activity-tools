# Contributor and agent guide

- Work on a feature branch and use a pull request; never push directly to main.
- Use red/green tests for behavioral changes. Keep fixtures synthetic: no real
  activities, accounts, routes, locations, tokens or production configuration.
- Run pytest and Ruff before committing. Preserve docs-only CI path filters.
- Documentation-only changes must not bump package/manifest versions, create
  release tags or publish GitHub releases. Normal HA installs track versioned
  releases; do not publish a new version just to clear a development-build prompt.
- Reuse the existing Strava integration's OAuth session and webhook subscription.
  Do not add copied tokens, another credential store or activity polling.
- Dry runs must not write. Verify ownership before disclosure or mutation, use
  narrow writes, preserve independent edits and never log raw provider bodies.
- Keep portable operations separate from HA adapters. Prefer native automations
  and reusable blueprints to a separate rule builder.
- workout_type is a deliberate compatibility exception to documented update
  fields: keep strict integer/sport validation, preview and read-back checks.
  Feed mute is not activity privacy.
- Do not deploy to a user's HA instance or change real activities unless the user
  authorizes that work. Publishing code does not authorize deployment.

## Helping someone install or configure this integration

Read [the agent setup guide](docs/AGENT_SETUP.md) before working on a live HA
instance. It covers HA MCP discovery, the provider prerequisite, account/scopes,
blueprints, history/dashboard setup, verification and rollback.

- Discover existing `ha_strava` and companion entries first. Never assume the
  first account is the intended one or create a duplicate to fix authorization.
- A loaded `ha_strava` connection with the required activity-created event is a
  prerequisite. The companion does not install it automatically. Explain missing
  prerequisites; never silently patch the provider or copy OAuth tokens.
- New installs keep both integration and automation previews enabled until the
  user asks to enable writes. Upgrades preserve existing settings and receipts.
- Use HA MCP's dedicated store/config-flow/blueprint/dashboard tools and native
  APIs. Do not edit `.storage`, request secrets in chat, or bypass store notices.
- Help the user find results: create an explicitly requested dashboard using
  native cards, or explain the integration's Latest preview/Latest run entities.
  History entities require the newer build described in the README.
- Verify a read-only preview, account isolation, enabled consumers, preserved job
  receipts and clean logs. A successful download is not proof the new code loaded.
  Never fire a synthetic activity-created event into live write automations.
- Report the installed version/ref, restart outcome, where results appear and any
  remaining user steps. Record only sanitized validation in this public repo.

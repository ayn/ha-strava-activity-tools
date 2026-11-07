# Contributor and agent guide

- Work on a feature branch and use a pull request; never push directly to main.
- Use red/green tests for behavioral changes. Keep fixtures synthetic: no real
  activities, accounts, routes, locations, tokens or production configuration.
- Run pytest and Ruff before committing. Preserve docs-only CI path filters.
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

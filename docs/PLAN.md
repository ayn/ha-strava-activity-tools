# Architecture and roadmap

The existing ha_strava integration owns OAuth and the Strava webhook. This
companion adds enrichment and native HA actions/blueprints, using that session
rather than storing another token or creating another subscription.

Portable transformations and the HTTP client are separate from HA recorder,
configuration, event and persistence adapters. Generic Strava improvements are
being proposed upstream separately; environmental enrichment stays here.

Current capabilities:

- Historical worldwide US EPA AQI/UV through Open-Meteo/CAMS, with optional local
  sensor history and source attribution.
- Preview-first owner-checked edits, feed mute and gear blueprints.
- Durable environment jobs, bounded retries, completed receipts and manual replay.
- Read-only interval/calendar matching and GPS track/shoe suggestions.

Next work includes broader live validation, an HA support matrix, catalog
installation/update checks, clearer preview/repair UI, and matching for tempo,
progression, doubles and separate warm-up uploads. Automatic workout/shoe writes
need their own durable handling and validation.

The initial repository is experimental and can be installed as a custom
repository. Default Marketplace listing is a later step. Existing rule services
can continue handling rules that have not been migrated.

No AI activity analysis, route database, coaching platform or activity-copying
service is included. A possible HA Core path for the Strava provider is separate
from this companion and has no promised acceptance or schedule.

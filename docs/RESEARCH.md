# Strava ecosystem research

Research date: 2026-11-06. This is a feature comparison, not an active-user ranking:
comparable public adoption data is not available for these services. Advertised
features are not evidence that their public API access can be reproduced by a
new developer application. Prefer maker documentation over popularity anecdotes.

## Products and useful lessons

| Product | Documented focus | Useful lesson for this project |
| --- | --- | --- |
| [Strautomator](https://strautomator.com/home) | Conditional edits, mute, gear, weather, templates, counters, AI and music additions | Cover simple daily rules first; readable templates and reusable presets |
| [ActivityFix](https://activityfix.com/) / [FAQ](https://activityfix.com/faq) | Multi-condition rules, activity updates, gear, sensor/device criteria, Test Rule | Preview matches and changes before enabling; distinguish exact/contains and AND/OR |
| [Klimat](https://klimat.app/features) | Configurable weather/AQI/UV annotations and presentation | Preserve manual descriptions; historical readings; chosen units and placement |
| [Intervals.icu](https://www.intervals.icu/) | Training calendar, workout builder, load and interval analysis | Calendar context may help later; don't rebuild a coaching platform inside HA |
| [StatsHunters](https://www.statshunters.com/faq) | Read-only stats, heatmaps, tiles and filters | Good filtering and progress summaries; analysis is a separate product category |
| [VeloViewer](https://veloviewer.com/pro) | Detailed activity analysis, visualizations and Explorer features | Optional external summaries later; maps and segment analytics are outside the MVP |
| [Wandrer](https://wandrer.earth/) | Unique-road exploration, achievements and challenges | Useful possible notification source; not a reason to store route history in HA |
| [Elevate](https://github.com/thomaschampagne/elevate) | Fitness/progression analysis, desktop and browser tooling | Keep training analytics out of an automation integration; repository stars aren't active users |
| [HealthFit](https://apps.apple.com/us/app/healthfit/id1202650514) | Apple Health workout analysis and export/sync to fitness services | Source preservation and dependable delivery matter; Apple Health access belongs on the phone |
| [RunGap](https://apps.apple.com/us/app/rungap-workout-data-manager/id534460198) | Workout management and multi-service sharing | Explicit destinations, duplicate handling, and clear delivery state are useful patterns |

Squadrats was also investigated as an exploration/route-planning reference. Its
primary site returned an automated-access 403 during this research, so details
from third-party comparisons were not used to justify implementation decisions.
No claim is made that any of these services currently has a particular market share.

ActivityFix's extra sensor/profile conditions may require Garmin/Wahoo connections;
they are not all standard Strava activity fields. A uniform application-name field
is not guaranteed by the documented Strava API. Expose actual `device_name` and
title values, and support a title fallback for sources such as Tractive. Do not
pretend every record has source-app metadata.

## Initial feature priorities (2026-11-06)

**First:** dependable activity-created trigger; fetch by ID; account isolation;
optional scoped writes; mute; gear assignment; description edits; previews;
exact/contains and sport/distance conditions; visible errors; AQI/UV append.

**Next:** gear selector, friendly source inspection, persistent bounded retry jobs,
manual replay and preview UI, notification blueprints, distance/time unit selectors,
and reusable description templates. Keep the automation editor native to HA.

**Later:** optional workout-calendar correlation using timestamps, sequence and
session context; mileage reminders using an approved source; broader weather
sources for travel; independent enrichment beyond weather. All-day event titles
alone are weak evidence of whether a short run is warm-up or cooldown.

**Out of the first release:** AI activity titles/analysis, route/heatmap storage,
fitness modeling, cross-service activity copying, kudos/comments automation, and
undocumented privacy or map-style endpoints. Existing competitors advertising a
feature does not establish our API authorization for it.

## API and distribution findings

[Strava's public API reference](https://developers.strava.com/docs/reference/)
documents activity edits for title, description, sport, gear, trainer, commute,
and feed mute. `hide_from_home` is not activity privacy. Use documented form
fields, validate types, and avoid full-object writes. Fetching an activity by ID
must not depend on a sensor retaining it among the latest few activities.

[Webhooks](https://developers.strava.com/docs/webhooks/) are the right trigger;
acknowledge promptly and fetch details in the background. There is one subscription
per developer app. Never silently replace an existing callback. Duplicate and
out-of-order delivery, temporary detail unavailability, and rate limits need tests.

[Getting started](https://developers.strava.com/docs/getting-started/) currently
requires a Strava subscription to create a developer app. Apps start with only
the developer's athlete account; athlete capacity expansion and review affect
public onboarding. A widely distributed HA integration needs a deliberate OAuth
application model; sharing one credential across arbitrary installations is not
an implementation shortcut. Respect rate-limit headers rather than old fixed quotas.

The [current API policy](https://www.strava.com/legal/api_policy) materially limits
AI use of API data, credential reuse/abstraction layers, and retention. Seven-day
cache limits and deletion handling affect architecture. Obtain clarification
before public distribution; this document does not assert that a companion
integration is approved. Avoid copying a personal shared-token worker design into
a public product. Independent HA sensor history is a different data source.

[HA's integration guide](https://contributors.home-assistant.io/core/integrations/create/)
recommends Marketplace first, then a Core discussion after the integration matures.
A Core integration needs a suitable PyPI communication library and a minimal
Bronze-quality initial PR. It does not follow that a large rule toolkit belongs
in Core. [Marketplace publication](https://contributors.home-assistant.io/marketplace/guides/integrations/)
requires a proper custom integration repository and release, localization,
validation, and installation metadata. Blueprints remain separately importable
content rather than becoming an integration by themselves.

Strautomator's [November 4 changelog](https://strautomator.com/changelog) reports
removing some MCP tools for Strava terms compliance and moving to a monorepo.
Its current source tree is broader than an older "Core library" description suggests.
AQI was not found in the public feature documentation checked; absence from those
pages is not proof it is unavailable in every account or integration.

## Conclusion

Extend the existing Strava provider where possible, add small independent HA
enrichment modules where necessary, and share reusable native HA blueprints.
A second OAuth implementation or a custom IFTTT editor would add maintenance
without helping the initial use cases. Keep policy and public onboarding questions
explicit before a release, while developing and testing locally without live data.


## Follow-up: existing activity tooling (2026-11-07)

[Strautomator](https://github.com/ramadasnet/strautomator) is a public implementation
of a broad hosted rule system and a useful API compatibility reference.
[ActivityFix](https://activityfix.com/index) advertises gear/workout-type rules.
Neither primary feature description establishes a drop-in HA implementation of
our sensor-history enrichment and calendar/GPS matching.

[Strava Connect](https://github.com/CraigBell/strava_connect) is especially relevant:
its README documents a dynamic shoe catalog, Stryd mapping and
`ha_strava.set_activity_gear`. It keeps the `ha_strava` domain, so it replaces rather
than coexists with that provider. Its MIT license permits reuse with attribution,
but no implementation has been copied here. Compare concrete code/tests and
maintenance before proposing a provider migration or duplicate upstream changes.

[Stravalib](https://github.com/stravalib/stravalib) provides general Python API
access and mutations. Adopting it would need to preserve HA's existing async
OAuth ownership and avoid adding a second refresh-token manager. It is not a
calendar or track-classification engine. The initial search did not identify a
maintained, directly reusable HA GPS track detector; this is a scoped finding,
not a claim that none exists.

## Roadmap refresh (2026-11-07)

The initial priorities above are a historical research snapshot. Several are now
implemented: native mute/gear actions, durable fetch/environment jobs, worldwide
historical AQI/UV, and conservative calendar/track/shoe suggestions. Remaining
work is tracked in the [roadmap issue](https://github.com/ayn/ha-strava-activity-tools/issues/5)
and indexed in [PLAN.md](PLAN.md); ideas are not delivery commitments.

The latest primary feature pages reinforce these practical directions:

- [ActivityFix’s FAQ](https://activityfix.com/faq) describes an explicit Test Rule
  workflow, trainer/virtual-ride distinctions and device/sensor conditions. Bring
  clear previews and explanations to native HA actions, while distinguishing
  actual Strava fields from data requiring another provider connection.
- [Strautomator](https://strautomator.com/home) advertises conditional names,
  commute/workout tags, feed mute, gear selection, weather, counters and gear
  maintenance reminders. Reusable HA blueprints, friendly gear selection,
  deterministic templates and deduplicated threshold notifications fit this
  project better than a second rule editor or another OAuth implementation.
- [Klimat](https://klimat.app/features) lists optional weather metrics, configurable
  units/templates and annotation positions. Offer opt-in historical fields and
  presentation choices while preserving notes, attribution and minimal requests.

Reliability work comes first: durable generic writes, safe lifecycle cleanup and
clear onboarding/repair. More complex calendar matching, edit-event handling,
cycling and maintenance reminders are proposals with data/API investigations in
their issues. Advertised competitor features do not prove our API access or
establish demand; ask users for concrete examples before widening scope. AI
activity-data analysis, route-history storage and undocumented privacy/map writes
remain outside this roadmap.

## Planned-workout interoperability opportunity

[Issue #22](https://github.com/ayn/ha-strava-activity-tools/issues/22) explores
reusing Workoutbridge/Prescribed.fit structured plans for completed-activity
matching. The [public shared model](https://github.com/ayn/workoutbridge/blob/main/workoutbridge/parser.py)
already represents typed steps/repeats and review state; the
[current web API](https://github.com/ayn/workoutbridge/blob/main/docs/web-api.md)
is explicitly provisional and does not provide a stable external-client contract.

Start with an optional read-only contract shared by hosted and self-hosted
adapters. Keep matching portable for HA and service backends. Completion feedback
would be a separate opt-in phase, with revision/identity checks and idempotency;
private coach text must not automatically become a public activity description.
This is a proposal, not existing functionality or a hosted-service requirement.

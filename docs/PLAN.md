# Architecture and roadmap

The existing ha_strava integration owns OAuth and the Strava webhook. This
companion adds enrichment and native HA actions/blueprints, using that session
rather than storing another token or creating another subscription.

Portable transformations and the HTTP client are separate from HA recorder,
configuration, event and persistence adapters. Generic Strava improvements are
proposed separately: [activity events #349](https://github.com/craibo/ha_strava/pull/349),
[deterministic retry test #351](https://github.com/craibo/ha_strava/pull/351),
[legacy reauthorization #352](https://github.com/craibo/ha_strava/pull/352), and
[account-scoped fetch/write #353](https://github.com/craibo/ha_strava/pull/353).
These PRs are ready for review, not merged. The companion currently uses its own
client with the provider’s existing OAuth session; #353 is not an installation
prerequisite. Environmental enrichment stays here.

Current capabilities:

- Historical worldwide US EPA AQI/UV through Open-Meteo/CAMS, with optional local
  sensor history and source attribution.
- Preview-first owner-checked edits, feed mute and gear blueprints.
- Durable environment jobs, bounded retries, completed receipts and manual replay.
- Read-only interval/calendar matching and GPS track/shoe suggestions, with
  expected-state checks for explicitly configured native write automations.

## Planned work

The pinned [GitHub roadmap](https://github.com/ayn/ha-strava-activity-tools/issues/5)
is the tracking index. Linked issues hold acceptance criteria, discussion and
current status; this document describes the proposed order, not delivery dates.
The roadmap draws on [the ecosystem research](RESEARCH.md), refreshed against
primary feature pages on November 7, 2026. Competitor feature availability is not
proof of API access or measured user demand.

### Foundations and release readiness

| Issue | User benefit |
| --- | --- |
| [#6: Complete clean-install and compatibility validation for catalog submission](https://github.com/ayn/ha-strava-activity-tools/issues/6) | A clean install with released dependencies, then catalog submission. |
| [#7: Make mute, workout-tag and gear jobs durable across HA restarts](https://github.com/ayn/ha-strava-activity-tools/issues/7) | Mute/tag/gear work survives restarts and uncertain responses. |
| [#8: Clean up queued data safely when accounts are removed or deauthorized](https://github.com/ayn/ha-strava-activity-tools/issues/8) | Removal and deauthorization stop work and clean up private stores. |

### Next useful features

| Issue | User benefit |
| --- | --- |
| [#9: Guide onboarding and diagnose source permissions without manual IDs](https://github.com/ayn/ha-strava-activity-tools/issues/9) | Verified account selection, permission checks and helpful repair paths. |
| [#10: Expose processing health, previews and repair through native HA controls](https://github.com/ayn/ha-strava-activity-tools/issues/10) | Understand matching decisions, inspect health and retry selected work. |
| [#11: Share Workout-tag and track-shoe automation blueprints](https://github.com/ayn/ha-strava-activity-tools/issues/11) | Import a complete guarded Workout-tag/track-shoe automation. |
| [#12: Add rule exclusions, richer filters and metric-friendly units](https://github.com/ayn/ha-strava-activity-tools/issues/12) | Use km/miles and preserve exceptions such as short races. |
| [#13: Add shoe/bike pickers and reusable gear-assignment presets](https://github.com/ayn/ha-strava-activity-tools/issues/13) | Choose shoes/bikes by name and share assignment presets. |
| [#14: Support previewable activity title and description templates](https://github.com/ayn/ha-strava-activity-tools/issues/14) | Format titles and annotations without AI or another rule language. |
| [#15: Add optional historical weather fields and annotation formatting](https://github.com/ayn/ha-strava-activity-tools/issues/15) | Choose extra historical metrics, units and presentation. |
| [#16: Recognize continuous tempo and progression workouts conservatively](https://github.com/ayn/ha-strava-activity-tools/issues/16) | Recognize supported quality sessions beyond explicit repeat intervals. |
| [#24: Apply the matched workout title](https://github.com/ayn/ha-strava-activity-tools/issues/24) | Rename generic activity titles after a confident match, with manual-title protection. |

### Later and exploratory

| Issue | User benefit |
| --- | --- |
| [#17: Distinguish doubles and separate warm-up/cooldown uploads](https://github.com/ayn/ha-strava-activity-tools/issues/17) | Use session context to distinguish uploads on the same day. |
| [#18: Add gear mileage and maintenance reminders using native HA notifications](https://github.com/ayn/ha-strava-activity-tools/issues/18) | Notify on trustworthy gear distance/time thresholds. |
| [#19: Design opt-in activity-update handling that respects manual overrides](https://github.com/ayn/ha-strava-activity-tools/issues/19) | React to useful edits without loops or overriding manual choices. |
| [#20: Extend calendar-assisted Workout tagging to cycling sessions](https://github.com/ayn/ha-strava-activity-tools/issues/20) | Bring conservative matching to supported cycling activity types. |
| [#22: Structured planned-workout integration](https://github.com/ayn/ha-strava-activity-tools/issues/22) | Reuse Workoutbridge/Prescribed.fit plans for optional completion matching. |

## Implementation boundaries

Use native HA config flows, actions, selectors, notifications and blueprints.
Reuse the provider’s OAuth and webhook; preserve previews, ownership checks,
manual edits and minimal bounded storage. Add red/green synthetic regressions
for behavioral work and explicit opt-ins for new writes. Generic provider work
belongs upstream where appropriate.

These priorities are project choices. The formal catalog prerequisites and
upstream-release gates remain in [PUBLISHING.md](PUBLISHING.md). A catalog listing
is not needed for custom-repository installation, and no acceptance date is promised.

A custom IFTTT-style editor, independent OAuth store, recurring activity polling,
AI activity-data analysis, route/history database, coaching platform, cross-service
activity copying and undocumented privacy/map writes are outside this roadmap.
A possible Core path for the provider remains separate from the companion.

# Terminal-receipt handover patch 0.7.1

Marketplace requires removing old-domain config entries before repository
uninstall. An explicit admin/MCP action now retains validated terminal receipts
across that removal. It requires an active matching native migration proof and
preview mode, rejects nonterminal work and corrupt hashes, and performs no API
reads/writes. Red/green regressions cover old-entry removal, reload, idempotency,
account isolation and HA administrator permissions. The full suite passes 431 tests
with 91.94% coverage.

# Standalone 0.7.0 validation

The standalone release package currently passes **424 tests**, **91.96% line
coverage**, Ruff check and Ruff format checks. Its suite covers native HA
OAuth/config flows, HTTP callbacks, subscription lifecycle, durable ingress,
account isolation, preview/read-back guards, retries, setup failures, disabled
account races, history sensors, recorder SQL and packaged blueprint execution.
Fixtures are synthetic; these tests do not mutate real Strava activities.

The preceding development checkpoint passed 731 tests plus eight skipped legacy
provider contracts with 92.51% combined coverage. At promotion, duplicated legacy
companion tests and contracts tied to the removed external dependency were retired;
portable operation, history, workout-exemption and native boundary regressions run
against the new shipping component. The standalone coverage gate remains 90%.
Skipped contracts were never counted as passing coverage of that dependency.

The published custom-component harness pins HA 2026.10.4. Installing it with
HA 2026.11.2 fails while importing changed framework logger APIs, before test
collection. This is not a current-version matrix pass. Live deployment, browser
consent and callback checks are distinct verification steps, recorded separately
when performed. No live deployment is claimed by this packaging checkpoint.

See [contributing](../CONTRIBUTING.md), [compatibility](COMPATIBILITY.md) and
[provenance](PROVENANCE.md). Remaining historical records below describe earlier
companion releases and are not standalone deployment claims.

---

## Historical companion validation

### Validation

The tested baseline is Home Assistant 2026.10.4, Python 3.14 and the matching
custom-component harness pinned in requirements-dev.txt. Newer HA versions
need separate runtime validation.

Tests use synthetic activities, routes, accounts, credentials and calendar text.
They exercise HA's actual service registry, configuration flow, event bus,
automation engine, blueprint loading and recorder history, plus local HTTP
servers for request encoding and response verification.

Coverage includes ownership checks, preview locks, concurrent edits, duplicate
delivery, uncertain writes, malformed input, bounded durable jobs, reload
persistence, account isolation, historical timestamps, actual zero UV, API
attribution and source-selection compatibility.

Calendar and GPS matching are heuristics. Tests do not establish measured
classification accuracy or guarantee a track-shaped route is a running track.
Suggestion actions remain read-only. An explicitly configured native automation
can use their proposed changes and `expected` state in the update action.
Preview defaults do not imply that every deployed automation is read-only.

Live checks have covered installation, OAuth reuse, historical lookups, same-value
provider write permission, job persistence and webhook deduplication. Fresh
API-derived annotations after upload need broader live coverage. This is not
default catalog approval or provider endorsement.

The initial public snapshot excludes internal development history and operational
configuration. Run pytest with coverage and Ruff before proposing changes.

## Public-domain migration validation (0.6.0)

The renamed component passes 241 tests with 93.14% statement coverage on the
tested HA baseline. Native Store/config-flow tests cover owner checks, disabled
legacy entries, completed-receipt copying, idempotence, corrupt/pending refusal,
and duplicate webhook suppression after migration. Ruff lint and formatting pass.
The sanitized snapshot passed Gitleaks and explicit private-identifier checks;
no production history is included in this repository.

## Guarded writes (0.6.1)

The full suite passes 251 tests with 93.26% statement coverage on the tested
baseline. New cases cover intervening manual labels/shoes, indoor flag changes,
typed preconditions, the integration preview lock and idempotent no-op updates.
Ruff and GitHub CI pass; Gitleaks scans of the snapshot and public history pass.

HACS installation, retained receipts, selected-shoe catalog lookup and native
muting/workout consumers have been checked live. Sampled dog walks, short runs
and Peloton stretches match the intended rules; a sampled bike ride does not.
A correctly labeled track workout completes the native workflow without changes.
Broader positive classification writes and fresh API-derived annotations after
upload still need live coverage. Native muting/tag/shoe consumers are separate
from the durable fetch/environment queue.

## Preview and history support (0.6.2)

The full suite passes with 268 tests and 93.91% coverage. Ruff lint and formatting
checks pass, as does a Gitleaks scan of the repository files. The first journal
regressions were added before implementation and failed because the module did
not exist.

New tests exercise actual HA platform setup, device/entity registration, service
results, the integration preview lock, background retries and recovery, reloads,
failed platform unload, account removal and timer-driven history expiry. The
standard Markdown card is rendered with HA's template engine against real entity
states, including empty and removed-account states. Journal tests cover bounded
retention, owner changes, redaction and storage/listener failures that must not
interrupt successful writes. Existing blueprint and durable-job tests still pass.

The tested main build was subsequently installed through the HACS custom
repository on one private HA 2026.10.4 instance. After restart, both history
entities loaded, existing options and job receipts were preserved, existing
consumers retained their enabled states, and integration error logs were clear.
A read-only track preview appeared in the new history entity and a native
dashboard. Saved dashboard configuration and live Markdown rendering were
verified. Processing history begins with new actions; old receipts were not
backfilled. No new Strava mutation was performed for this validation.

Version 0.6.2 is an early-access custom-repository release, not a default catalog release. Dashboard
screenshots were unavailable on that instance; live template rendering is not
a browser screenshot or a claim of visual validation across screen sizes.


## Durable mute outage recovery (v0.6.4)

Red: the native mute blueprint failed `source_not_loaded` before saving any job;
the restart/cooldown contract rejected `mute` as an invalid job. Green: matching
and narrow mute now share one stored intent, retry a missing source and rate
limits, and retain filters across reload. Tests cover duplicate delivery, preview
locks on queued work, blank/malformed filters, ownership and a title edited between
reads. No synthetic webhook is fired into a live write automation.

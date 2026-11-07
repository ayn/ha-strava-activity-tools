# Validation

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
Automatic workout-tag and track-shoe writes are not enabled by these previews.

Live checks have covered installation, OAuth reuse, historical lookups, same-value
provider write permission, job persistence and webhook deduplication. Fresh
API-derived annotations after upload need broader live coverage. This is not
default catalog approval or provider endorsement.

The initial public snapshot excludes internal development history and operational
configuration. Run pytest with coverage and Ruff before proposing changes.

## Public-domain migration validation

The renamed component passes 241 tests with 93.14% statement coverage on the
tested HA baseline. Native Store/config-flow tests cover owner checks, disabled
legacy entries, completed-receipt copying, idempotence, corrupt/pending refusal,
and duplicate webhook suppression after migration. Ruff lint and formatting pass.
The sanitized snapshot passed Gitleaks and explicit private-identifier checks;
no production history is included in this repository.

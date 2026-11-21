# Compatibility boundary

Strava Automation owns its native HA OAuth session, webhook and activity client.

The full suite exercises native HA config flows, signed OAuth callbacks, token
rotation, webhook HTTP routing, subscriptions, services, automations, blueprints,
recorder SQL history, entry lifecycle and private durable stores. Loopback APIs
and synthetic fixtures verify the Strava protocol boundary without live writes.

The reproducible development harness currently pins HA 2026.10.4. It cannot simply
be installed with HA 2026.11.2: its framework fixtures import logger APIs removed
in the newer version. Do not patch away missing fixture APIs or claim that failed
collection passed. Upgrade the framework/harness together when a matching harness
is published. Live current-version checks are documented separately in validation.

The allowlisted `workout_type` update is intentionally beyond Strava's documented
update schema. Strict sport/field validation and read-back reduce risk, but do not
constitute a guarantee of permanent upstream support. The same applies to feed
muting and any provider policy changes. Do not silently exempt failing regressions.

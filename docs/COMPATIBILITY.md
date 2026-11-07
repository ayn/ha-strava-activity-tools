# Testing against ha_strava

The ordinary suite substitutes the provider coordinator/OAuth boundary. The
separate **Provider compatibility** workflow also checks a real provider checkout
with both integrations loaded through HA's config-entry lifecycle.

## What is covered

Seven synthetic contract cases exercise:

- Actual HTTP challenge/POST handling, provider event emission and companion
  durable fetching through the real provider coordinator's OAuth session.
- Duplicate deliveries, shared-subscription account routing, and rejection of
  wrong owners/subscriptions and activity-update notifications.
- API ownership verification even when a forged webhook names another configured
  account. An accepted webhook alone cannot authorize disclosure or mutation.
- Preview defaults and a narrow, verified form write to a local fake Strava server.
- Provider unload/reload: calls fail closed while unloaded and rebind to the new
  session afterward.

Provider startup/background refresh data and subscription registration are
substituted to avoid external Strava traffic. Provider setup, coordinator/OAuth
construction, HTTP receiver, event bus, platform lifecycle and companion operations
run normally. The fake API sees the expected synthetic account's Bearer token.
This is not a real OAuth login, token-endpoint test, production upload or a claim
to cover every provider feature. Existing unit tests still cover safe reauth errors.

## Pinned baseline and release checks

`provider-compatibility.json` pins the reviewed event-capable provider build by
full commit SHA. It currently points to the branch behind upstream PR #349,
not to a published upstream release. Review and advance this pin when the event
is released upstream; do not float the passing baseline to main automatically.

- Relevant pull requests and main pushes run the pinned baseline. Markdown-only
  edits do not trigger it. Compatibility changes do not require a component release.
- Every six hours, the default-branch workflow queries GitHub's latest stable
  `ha_strava` release and resolves its tag to an immutable commit before checkout.
  It never polls Strava or runs on a user's HA hardware. GitHub can delay schedules;
  this is not an instantaneous upstream-release trigger.
- Passing release/code combinations are cached. New tags/commits or relevant
  companion/contract changes trigger tests; failed combinations are not cached
  and will be retried. Documentation changes are excluded from that code cache key.
- The exact known released build `4.6.7` at its recorded commit lacks the required
  event. The workflow reports **known missing prerequisite**, not compatible, and
  skips normal contract execution for that one exact combination. New versions or
  moved tags are never silently exempted. A missing event on a new release fails
  the contract with a prerequisite-specific message.
- Manual dispatch supports `baseline`, `latest-release`, and `upstream-main`.
  Set **force** to rerun cached targets or demonstrate a known-unsupported build's
  failure. Upstream main is a canary, not a supported-version claim.

Each run records the repository, tag/target, exact SHA and outcome in its summary.
Failures use standard Actions notifications according to the maintainer's settings;
the workflow does not open issues, assign Copilot, deploy to HA or publish releases.

A GitHub `release` event applies to the repository containing the workflow. An
immediate cross-repository trigger would require an agreed dispatch hook from the
provider or an external webhook service. The scheduled watcher works independently.
Copilot/Agentic Workflows can be added separately for opt-in failure diagnosis and
proposed fixes; the compatibility result should remain a deterministic test result.

## Local use

Use a disposable, public-source provider checkout, not production configuration:

```sh
python -m pip install -r requirements-dev.txt -r requirements-provider-test.txt
# Also install the selected provider manifest's runtime requirements.
HA_STRAVA_SOURCE=/absolute/path/to/ha_strava python -m pytest tests/provider_contract -q
```

Without `HA_STRAVA_SOURCE`, the opt-in cases are explicitly skipped; they cannot
be claimed as passing. An invalid supplied path fails. CI installs the provider's
declared requirements and the pinned camera dependency for the tested HA baseline.

Use only synthetic fixture accounts, credentials and activity data. Do not provide
production OAuth secrets to these jobs. Both checkouts disable persisted GitHub
credentials, jobs have read-only repository permissions, and authentication used
for release discovery is scoped to that step.

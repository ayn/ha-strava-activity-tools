# HACS and Marketplace readiness

One repository, domain and release stream serves both HACS and the built-in
Marketplace. They share the `hacs/default` catalog and the `hacs.json` manifest.
The README uses the older `hacs_repository` My Home Assistant link because it
opens HACS before HA 2026.11 and Marketplace on newer releases. Blueprints use
separate import links; they are not catalog entries.

## Preparation in this repository

- Public MIT repository, issues, topics, README, config flow and complete English
  translations.
- Local original icon assets in `custom_components/strava_activity_tools/brand/`.
  Editable artwork is in `assets/icon.svg`; export at 256 and 512 square pixels.
- The `health_wellbeing` integration category and matching project/manifest version.
- HACS validation with `category: integration` and no ignored checks, plus hassfest.
  `.github/workflows/publish-validation.yml` runs them on relevant pull requests,
  relevant pushes to main, and manual dispatch. Documentation-only commits skip CI.
- Existing synthetic HA tests, Ruff and coverage checks remain in `checks.yml`.

A validator run is evidence only after it passes; the workflow definition alone
is not validation. Neither custom-repository installation nor passing validators
means the catalog team has approved the integration.

Track the remaining release gates in [issue #6](https://github.com/ayn/ha-strava-activity-tools/issues/6).
Feature proposals and reliability work are indexed in [PLAN.md](PLAN.md).

## Releases and update prompts

- Documentation-only changes (including README, AGENTS.md and setup guides) do
  not bump the project/manifest version and do not create tags or GitHub releases.
  Existing Markdown-only CI filters must continue to skip those commits.
- Cut a release for changes users need to install: integration behavior, shipped
  blueprints, assets, or required packaging/compatibility fixes. Keep published
  release tags immutable. Do not release every merged PR.
- Recommend versioned releases for normal installations. A docs commit on main
  does not change an installed release tag. HACS development-branch installs can
  react to commits, including docs commits, and commit hashes do not compare as
  semantic release versions. Do not promise release-style filtering for main.
- If HACS offers an older stable release after a deliberately installed development
  build, verify both versions before proceeding. Skipping that specific older
  version avoids an unintended downgrade without hiding later release versions.
  Do not reset receipts, disable the update entity, or publish a release solely
  to silence this mismatch.

Legacy HACS update entities may still request icons from the external brands
site instead of HA's local brands API, displaying an “icon not available”
placeholder for integrations with packaged branding. The integration ships its
own assets; verify the requested image URL before changing those files. Track
the upstream behavior in [hacs/integration #5171](https://github.com/hacs/integration/issues/5171).
Do not force a Core/store upgrade or add a second updater to work around an icon.

## Before catalog submission

1. Wait for an upstream `ha_strava` release containing the activity-created event
   from [#349](https://github.com/craibo/ha_strava/pull/349). A clean installation
   must not require the user to patch provider files. The legacy fix
   [#352](https://github.com/craibo/ha_strava/pull/352) helps older accounts; generic
   fetch/write [#353](https://github.com/craibo/ha_strava/pull/353) is not currently
   required by the companion. The independent retry test is
   [#351](https://github.com/craibo/ha_strava/pull/351).
2. Test a clean install, updates, retained receipts and preview/write choices with
   that released provider on HA 2026.10.4 and Marketplace-era HA. The automated
   baseline is 2026.10.4. A live 2026.11.1 smoke check verified loading, native
   actions and retained receipts across restart, but does not establish a full
   clean-install/update matrix for the newly released fork. The currently
   published custom-component test harness targets 2026.10.4.
3. Finish broad positive live-write validation with synthetic public fixtures
   and private operational checks. Keep limitations explicit: matching can abstain,
   and native tag/shoe retries are separate from the durable mute and
   fetch/environment queues.
4. Pass HACS validation without `ignore` and pass hassfest on the final revision.
   Then publish a full GitHub release with a tag matching the manifest version.
   Version 0.6.2 is an early-access custom-repository release of the tested
   preview/history build. It does not satisfy the clean-install/released-provider
   gate for catalog submission or general onboarding.
5. Fork `hacs/default`, branch from `master`, and add
   `ayn/ha-strava-activity-tools` alphabetically to its `integration` list. Submit
   from the owner's account with maintainer edits enabled. Complete the template
   with the release and passing action-run links; do not request reviewers.

The tagged `ayn/ha_strava` fork provides a separate, documented path for fresh
early-access installations. It does not satisfy the upstream-release gate above,
automatically install a provider, or provide an in-place source transfer for an
existing Marketplace-managed upstream account. See the README's provider-first
instructions and existing-installation caveat.

Catalog submission is intentionally pending the upstream release and installation
validation. Early-access custom-repository releases remain available to users
whose provider already meets the event prerequisite. Catalog review can take months; no acceptance date is promised.

References: [catalog submission](https://contributors.home-assistant.io/marketplace/guides/catalog/),
[custom integration publishing](https://contributors.home-assistant.io/marketplace/guides/integrations/),
[validation](https://contributors.home-assistant.io/marketplace/reference/validation/),
[brand assets](https://contributors.home-assistant.io/core/integrations/brands/).

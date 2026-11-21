# Publishing

The repository is distributed as a custom **Integration** repository through
both older HACS and newer HA Marketplace. It is not yet listed in their default
catalog. Installation buttons in the README point to the same GitHub repository
and versioned release, with domain `ha_strava_automation`.

Before a release, run the full suite with the required coverage, Ruff, HACS
packaging validation and hassfest. Review the complete diff for credentials,
production fixtures, account leaks, unsafe lifecycle work and incompatible changes.
Version 0.7.0 changes the component domain; document the explicit migration before
releasing it. There must be exactly one shipped component directory in the root.

Use a feature branch, PR, green checks and reviewed merge; never push directly to
main. Only then tag/publish the matching version. Documentation-only changes do
not run product checks, bump versions, create tags or announce an update.
Publishing a release does not authorize installing it in anyone's HA instance.

A later default-catalog submission should follow the store's current requirements
and use the repository's public synthetic metadata, license, documentation and
issue tracker. Do not imply acceptance merely because custom installation works.

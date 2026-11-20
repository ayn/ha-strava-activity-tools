# Authentication implementation provenance

The independent `ha_strava_automation` authentication implementation and its tests
were authored from official Strava protocol documentation and installed native
Home Assistant framework APIs. The other provider's authentication implementation
and tests were not consulted or copied. Existing portable activity features are
reused from this repository's original companion code and retain its MIT history.

The authoring agent had previously read the provider's architecture guide while
planning. This is disclosed rather than claiming a formal clean-room process.
The supported connection migration reads native HA OAuth2Session objects in HA
memory and uses native Application Credentials/config-entry APIs. It does not
import the other provider's authentication modules or export credentials.

Primary references:

- [Strava authentication](https://developers.strava.com/docs/authentication/)
- [Strava webhooks](https://developers.strava.com/docs/webhooks/)
- [HA configuration flows](https://contributors.home-assistant.io/core/integrations/config-flow/)
- [HA native OAuth2 framework](https://github.com/home-assistant/core/blob/2026.10.4/homeassistant/helpers/config_entry_oauth2_flow.py)
- [HA Application Credentials](https://github.com/home-assistant/core/tree/2026.10.4/homeassistant/components/application_credentials)

HA's standard signed OAuth callback forwards state/code, not an arbitrary scope
query parameter. This integration verifies initial grants from Strava's documented
authenticated token response. Missing/malformed grants fail closed. Native sessions
own refresh locking, rotation and persistence. Tests include signed callbacks,
wrong identities, missing scopes, rejection/timeout cases and refresh concurrency.

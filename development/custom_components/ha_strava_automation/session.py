"""HA-specific OAuth adapter, isolated from the portable activity client."""

from typing import Any

from homeassistant.exceptions import OAuth2TokenRequestReauthError
from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session

from .auth import granted_scopes
from .client import ActivityError


class HASession:
    """Keep token refresh and storage in its existing configuration entry."""

    def __init__(self, session: OAuth2Session, budget=None) -> None:
        self.session = session
        self.budget = budget

    async def async_request(self, method: str, url: str, **kwargs: Any) -> Any:
        """Map invalid-grant failures to an actionable, non-sensitive category."""
        try:
            await self.session.async_ensure_token_valid()
            scopes = granted_scopes(self.session.token.get("scope"))
            if method.upper() != "GET" and "activity:write" not in scopes:
                raise ActivityError("write_scope_or_access_denied")
            if self.budget is not None:
                await self.budget.reserve()
            response = await self.session.async_request(method, url, **kwargs)
            if self.budget is not None:
                try:
                    await self.budget.observe(response.headers, response.status)
                except OSError:
                    response.release()
                    raise ActivityError("job_storage_unavailable") from None
            return response
        except OAuth2TokenRequestReauthError as err:
            raise ActivityError("reauth_required") from err

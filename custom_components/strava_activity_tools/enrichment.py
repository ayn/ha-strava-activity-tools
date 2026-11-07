"""Connect independent enrichment to a generic description transformation."""

from typing import Any

from .client import ActivityClient, ActivityError
from .environment import append_environment


async def enrich_description(
    client: ActivityClient,
    activity_id: str,
    aqi: Any,
    uv: Any,
    dry_run: bool = True,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    """Calculate on the current description, preserving note edits on explicit retry."""

    def transform(activity: dict[str, Any]) -> dict[str, Any]:
        try:
            return {"description": append_environment(activity.get("description"), aqi, uv, source)}
        except ValueError as err:
            raise ActivityError("history_unavailable") from err

    return await client.transform(activity_id, transform, dry_run)

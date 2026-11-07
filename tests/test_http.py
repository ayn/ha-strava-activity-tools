"""Verify the request/response contract against a real local aiohttp server."""

from unittest.mock import patch

import pytest
from aiohttp import ClientSession, web

from custom_components.strava_activity_tools.client import ActivityClient


@pytest.mark.parametrize("changes", [{"hide_from_home": True}, {"workout_type": 3}])
async def test_real_http_form_write_and_post_write_verification(
    socket_enabled, aiohttp_server, changes
):
    activity = {
        "id": 9,
        "athlete": {"id": 42},
        "sport_type": "Run",
        "name": "Run",
        "hide_from_home": False,
    }
    requests = []

    async def handle(request):
        assert request.match_info["activity_id"] == "9"
        requests.append(request.method)
        if request.method == "PUT":
            assert request.content_type == "application/x-www-form-urlencoded"
            form = await request.post()
            assert dict(form) == {key: str(value).lower() for key, value in changes.items()}
            activity.update(changes)
        return web.json_response(activity)

    app = web.Application()
    app.router.add_route("*", "/api/v3/activities/{activity_id}", handle)
    server = await aiohttp_server(app)
    async with ClientSession() as session:

        class LocalOAuth:
            async def async_request(self, method, url, **kwargs):
                return await session.request(method, url, **kwargs)

        with patch(
            "custom_components.strava_activity_tools.client.BASE_URL",
            str(server.make_url("/api/v3")),
        ):
            result = await ActivityClient(LocalOAuth(), "42").update("9", changes, False)
    assert result["status"] == "updated"
    assert requests == ["GET", "GET", "PUT", "GET"]

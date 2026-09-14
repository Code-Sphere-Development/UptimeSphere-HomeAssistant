"""Tests for the UptimeSphere HTTP client."""

from __future__ import annotations

from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest

from custom_components.uptimesphere.api import (
    StaticTokenAuth,
    UptimeSphereApiError,
    UptimeSphereAuthError,
    UptimeSphereClient,
    UptimeSphereConnectionError,
    UptimeSphereNoTeamError,
    UptimeSphereParseError,
    normalize_base_url,
)

from .conftest import API, BASE_URL, TOKEN, load


@pytest.fixture
def client(hass, mock_api) -> UptimeSphereClient:
    return UptimeSphereClient(
        async_get_clientsession(hass), BASE_URL, StaticTokenAuth(TOKEN)
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://uptime.example.com", "https://uptime.example.com"),
        ("https://uptime.example.com/", "https://uptime.example.com"),
        ("https://uptime.example.com/api/v1", "https://uptime.example.com"),
        ("uptime.example.com", "https://uptime.example.com"),
        ("http://10.0.0.5:8000/monitors", "http://10.0.0.5:8000"),
        ("  https://uptime.example.com  ", "https://uptime.example.com"),
    ],
)
def test_normalize_base_url(raw: str, expected: str) -> None:
    assert normalize_base_url(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "ftp://example.com", "://nope"])
def test_normalize_base_url_rejects_garbage(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_base_url(raw)


async def test_get_user_is_not_wrapped(client: UptimeSphereClient) -> None:
    assert (await client.async_get_user())["id"] == 7


async def test_get_dashboard_is_flat(client: UptimeSphereClient) -> None:
    dashboard = await client.async_get_dashboard()
    assert dashboard["total"] == 3
    assert dashboard["avg_response_time_ms"] == 200


async def test_monitors_walks_every_page(client: UptimeSphereClient, mock_api) -> None:
    """The list endpoint is double wrapped and pages at a fixed 20 per page."""
    monitors = await client.async_get_monitors()

    assert [monitor["id"] for monitor in monitors] == [1, 2]
    requested_pages = [
        call[1].query.get("page")
        for call in mock_api.mock_calls
        if call[1].path.endswith("/monitors")
    ]
    assert requested_pages == ["1", "2"]


async def test_uptime_keys_pass_through(client: UptimeSphereClient) -> None:
    uptime = await client.async_get_uptime(1)
    assert uptime["24h"] == 99.5
    # A window with no results at all is null, not zero.
    assert uptime["365d"] is None


async def test_latest_result_unwraps_the_double_envelope(
    client: UptimeSphereClient,
) -> None:
    result = await client.async_get_latest_result(1)
    assert result is not None
    assert result["response_time_ms"] == 142
    assert result["ssl_info"]["expires_in_days"] == 45


async def test_latest_result_without_history(hass, aioclient_mock) -> None:
    empty = load("history")
    empty["data"]["data"] = []
    aioclient_mock.get(f"{API}/monitors/9/history", json=empty)

    client = UptimeSphereClient(
        async_get_clientsession(hass), BASE_URL, StaticTokenAuth(TOKEN)
    )
    assert await client.async_get_latest_result(9) is None


async def test_401_raises_auth_error(hass, aioclient_mock) -> None:
    aioclient_mock.get(f"{API}/user", status=401, json={"message": "Unauthenticated."})
    client = UptimeSphereClient(
        async_get_clientsession(hass), BASE_URL, StaticTokenAuth(TOKEN)
    )

    with pytest.raises(UptimeSphereAuthError):
        await client.async_get_user()


async def test_403_no_team_is_its_own_error(hass, aioclient_mock) -> None:
    aioclient_mock.get(
        f"{API}/dashboard",
        status=403,
        json={"message": "You do not have access to any team for this application."},
    )
    client = UptimeSphereClient(
        async_get_clientsession(hass), BASE_URL, StaticTokenAuth(TOKEN)
    )

    with pytest.raises(UptimeSphereNoTeamError):
        await client.async_get_dashboard()


async def test_unrelated_403_is_not_a_no_team_error(hass, aioclient_mock) -> None:
    aioclient_mock.get(f"{API}/dashboard", status=403, json={"message": "Forbidden"})
    client = UptimeSphereClient(
        async_get_clientsession(hass), BASE_URL, StaticTokenAuth(TOKEN)
    )

    # Still an error, but not the one that drives the "no team" repair issue.
    with pytest.raises(UptimeSphereApiError) as excinfo:
        await client.async_get_dashboard()
    assert not isinstance(excinfo.value, UptimeSphereNoTeamError)


async def test_html_response_is_a_parse_error(hass, aioclient_mock) -> None:
    """A reverse-proxy login page must not look like a connection failure."""
    aioclient_mock.get(f"{API}/user", text="<!doctype html><title>Login</title>")
    client = UptimeSphereClient(
        async_get_clientsession(hass), BASE_URL, StaticTokenAuth(TOKEN)
    )

    with pytest.raises(UptimeSphereParseError):
        await client.async_get_user()


async def test_connection_failure(hass, aioclient_mock) -> None:
    aioclient_mock.get(f"{API}/user", exc=TimeoutError)
    client = UptimeSphereClient(
        async_get_clientsession(hass), BASE_URL, StaticTokenAuth(TOKEN)
    )

    with pytest.raises(UptimeSphereConnectionError):
        await client.async_get_user()


async def test_static_token_never_refreshes() -> None:
    """A Sanctum token cannot be renewed, so a 401 must reach the caller."""
    assert await StaticTokenAuth("x").async_refresh() is False
    assert await StaticTokenAuth("x").async_get_headers() == {
        "Authorization": "Bearer x"
    }

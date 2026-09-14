"""Detail coordinator load-control behaviour."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import API, load


async def test_detail_only_polls_registered_monitors(
    hass, init_integration: MockConfigEntry
) -> None:
    """Entities register their monitor; nothing else is ever fetched."""
    detail = init_integration.runtime_data.detail_coordinator

    assert detail.registered_monitors == {1, 2}


async def test_detail_stops_polling_a_monitor_when_its_entities_go_away(
    hass, init_integration: MockConfigEntry
) -> None:
    """Reference counted: removing one entity must not disable the others."""
    detail = init_integration.runtime_data.detail_coordinator

    # Monitor 1 has several detail entities (response time + uptime windows),
    # so a single unregister must not drop it.
    unregister = detail.register_monitor(1)
    unregister()
    assert 1 in detail.registered_monitors


async def test_detail_does_nothing_without_registrations(
    hass, mock_api, mock_config_entry: MockConfigEntry
) -> None:
    from datetime import timedelta

    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from custom_components.uptimesphere.api import StaticTokenAuth, UptimeSphereClient
    from custom_components.uptimesphere.coordinator import MonitorDetailCoordinator

    from .conftest import BASE_URL, TOKEN

    mock_config_entry.add_to_hass(hass)
    client = UptimeSphereClient(
        async_get_clientsession(hass), BASE_URL, StaticTokenAuth(TOKEN)
    )
    coordinator = MonitorDetailCoordinator(
        hass, mock_config_entry, client, timedelta(minutes=30)
    )

    mock_api.clear_requests()
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.data.uptime == {}
    assert mock_api.call_count == 0


async def test_one_failing_monitor_keeps_the_others(
    hass, aioclient_mock, init_integration: MockConfigEntry
) -> None:
    detail = init_integration.runtime_data.detail_coordinator
    await detail.async_refresh()
    await hass.async_block_till_done()
    assert detail.data.uptime[1]["24h"] == 99.5

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/monitors/1/uptime", exc=TimeoutError)
    aioclient_mock.get(f"{API}/monitors/1/history", exc=TimeoutError)
    aioclient_mock.get(f"{API}/monitors/2/uptime", json=load("uptime"))
    aioclient_mock.get(f"{API}/monitors/2/history", json=load("history"))

    await detail.async_refresh()
    await hass.async_block_till_done()

    assert detail.last_update_success
    # Monitor 1 keeps its previous values rather than blanking out.
    assert detail.data.uptime[1]["24h"] == 99.5
    assert detail.data.uptime[2]["24h"] == 99.5


async def test_auth_error_in_detail_is_fatal_for_the_entry(
    hass, aioclient_mock, init_integration: MockConfigEntry
) -> None:
    from homeassistant.exceptions import ConfigEntryAuthFailed
    import pytest

    detail = init_integration.runtime_data.detail_coordinator

    aioclient_mock.clear_requests()
    for monitor_id in (1, 2):
        aioclient_mock.get(
            f"{API}/monitors/{monitor_id}/uptime",
            status=401,
            json={"message": "Unauthenticated."},
        )
        aioclient_mock.get(
            f"{API}/monitors/{monitor_id}/history",
            status=401,
            json={"message": "Unauthenticated."},
        )

    with pytest.raises(ConfigEntryAuthFailed):
        await detail._async_update_data()

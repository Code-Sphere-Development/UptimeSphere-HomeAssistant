"""Setup, unload and repair-issue behaviour."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.uptimesphere.const import DOMAIN, ISSUE_NO_TEAM

from .conftest import API, load


async def test_setup_and_unload(hass, init_integration: MockConfigEntry) -> None:
    assert init_integration.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert init_integration.state is ConfigEntryState.NOT_LOADED


async def test_setup_retries_when_unreachable(
    hass, aioclient_mock, mock_config_entry: MockConfigEntry
) -> None:
    aioclient_mock.get(f"{API}/monitors", exc=TimeoutError)
    aioclient_mock.get(f"{API}/dashboard", exc=TimeoutError)

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_invalid_token_starts_reauth(
    hass, aioclient_mock, mock_config_entry: MockConfigEntry
) -> None:
    aioclient_mock.get(
        f"{API}/monitors", status=401, json={"message": "Unauthenticated."}
    )
    aioclient_mock.get(
        f"{API}/dashboard", status=401, json={"message": "Unauthenticated."}
    )

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )


async def test_no_team_creates_repair_issue(
    hass, aioclient_mock, mock_config_entry: MockConfigEntry
) -> None:
    """A 403 is a server-side session problem, not a bad token."""
    no_team = {"message": "You do not have access to any team for this application."}
    aioclient_mock.get(f"{API}/monitors", status=403, json=no_team)
    aioclient_mock.get(f"{API}/dashboard", status=403, json=no_team)

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Not SETUP_ERROR: re-entering the token would not help.
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_NO_TEAM) is not None


async def test_devices_are_grouped_under_the_instance(
    hass, init_integration: MockConfigEntry
) -> None:
    from homeassistant.helpers import device_registry as dr

    registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(registry, init_integration.entry_id)

    by_identifier = {next(iter(device.identifiers))[1]: device for device in devices}
    entry_id = init_integration.entry_id

    assert f"{entry_id}_1" in by_identifier
    assert f"{entry_id}_2" in by_identifier
    assert by_identifier[f"{entry_id}_1"].via_device_id is not None


async def test_new_monitor_gets_entities_without_a_restart(
    hass, aioclient_mock, init_integration: MockConfigEntry
) -> None:
    page2 = load("monitors_page2")
    page2["data"]["data"][0]["id"] = 3
    page2["data"]["data"][0]["name"] = "New monitor"

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/user", json=load("user"))
    aioclient_mock.get(f"{API}/dashboard", json=load("dashboard"))
    aioclient_mock.get(
        f"{API}/monitors", params={"page": "1"}, json=load("monitors_page1")
    )
    aioclient_mock.get(f"{API}/monitors", params={"page": "2"}, json=page2)
    for monitor_id in (1, 3):
        aioclient_mock.get(f"{API}/monitors/{monitor_id}/uptime", json=load("uptime"))
        aioclient_mock.get(f"{API}/monitors/{monitor_id}/history", json=load("history"))

    coordinator = init_integration.runtime_data.coordinator
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("sensor.new_monitor_status") is not None

"""Status-transition events and notification dispatch."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.uptimesphere.const import (
    CONF_NOTIFY_MONITORS,
    CONF_NOTIFY_ON,
    CONF_NOTIFY_SERVICE,
    EVENT_MONITOR_STATUS_CHANGED,
)

from .conftest import API, load


def _monitors(status_1: str, status_2: str = "down") -> tuple[dict, dict]:
    page1 = load("monitors_page1")
    page1["data"]["data"][0]["status"] = status_1
    page2 = load("monitors_page2")
    page2["data"]["data"][0]["status"] = status_2
    return page1, page2


async def _refresh_with(hass, aioclient_mock, entry, status_1: str) -> None:
    page1, page2 = _monitors(status_1)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/dashboard", json=load("dashboard"))
    aioclient_mock.get(f"{API}/monitors", params={"page": "1"}, json=page1)
    aioclient_mock.get(f"{API}/monitors", params={"page": "2"}, json=page2)

    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


async def test_no_event_on_the_first_refresh(
    hass: HomeAssistant, mock_api, mock_config_entry: MockConfigEntry
) -> None:
    """A restart must not replay every monitor as a fresh change."""
    events = async_capture_events(hass, EVENT_MONITOR_STATUS_CHANGED)

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert events == []


async def test_event_on_transition(
    hass: HomeAssistant, aioclient_mock, init_integration: MockConfigEntry
) -> None:
    events = async_capture_events(hass, EVENT_MONITOR_STATUS_CHANGED)

    await _refresh_with(hass, aioclient_mock, init_integration, "down")

    assert len(events) == 1
    data = events[0].data
    assert data["monitor_id"] == 1
    assert data["name"] == "Website"
    assert data["type"] == "https"
    assert data["old_status"] == "up"
    assert data["new_status"] == "down"
    assert data["entry_id"] == init_integration.entry_id


async def test_unchanged_status_fires_nothing(
    hass: HomeAssistant, aioclient_mock, init_integration: MockConfigEntry
) -> None:
    events = async_capture_events(hass, EVENT_MONITOR_STATUS_CHANGED)

    await _refresh_with(hass, aioclient_mock, init_integration, "up")

    assert events == []


async def test_transient_statuses_are_suppressed(
    hass: HomeAssistant, aioclient_mock, init_integration: MockConfigEntry
) -> None:
    """`pending` is passed through on every retry; it is not an outage."""
    events = async_capture_events(hass, EVENT_MONITOR_STATUS_CHANGED)

    await _refresh_with(hass, aioclient_mock, init_integration, "pending")

    assert events == []


@pytest.fixture
def notify_calls(hass: HomeAssistant) -> list:
    calls: list = []

    async def _handler(call) -> None:
        calls.append(call)

    hass.services.async_register("notify", "test_target", _handler)
    return calls


async def test_notify_service_called_for_selected_transition(
    hass: HomeAssistant,
    aioclient_mock,
    init_integration: MockConfigEntry,
    notify_calls: list,
) -> None:
    hass.config_entries.async_update_entry(
        init_integration,
        options={
            CONF_NOTIFY_SERVICE: "notify.test_target",
            CONF_NOTIFY_ON: ["down"],
            CONF_NOTIFY_MONITORS: [],
        },
    )
    await hass.async_block_till_done()

    await _refresh_with(hass, aioclient_mock, init_integration, "down")
    await hass.async_block_till_done()

    assert len(notify_calls) == 1
    assert "Website" in notify_calls[0].data["message"]


async def test_notify_skipped_for_unselected_transition(
    hass: HomeAssistant,
    aioclient_mock,
    init_integration: MockConfigEntry,
    notify_calls: list,
) -> None:
    hass.config_entries.async_update_entry(
        init_integration,
        options={
            CONF_NOTIFY_SERVICE: "notify.test_target",
            CONF_NOTIFY_ON: ["maintenance"],
            CONF_NOTIFY_MONITORS: [],
        },
    )
    await hass.async_block_till_done()

    await _refresh_with(hass, aioclient_mock, init_integration, "down")
    await hass.async_block_till_done()

    assert notify_calls == []


async def test_notify_restricted_to_selected_monitors(
    hass: HomeAssistant,
    aioclient_mock,
    init_integration: MockConfigEntry,
    notify_calls: list,
) -> None:
    hass.config_entries.async_update_entry(
        init_integration,
        options={
            CONF_NOTIFY_SERVICE: "notify.test_target",
            CONF_NOTIFY_ON: ["down"],
            CONF_NOTIFY_MONITORS: ["2"],
        },
    )
    await hass.async_block_till_done()

    await _refresh_with(hass, aioclient_mock, init_integration, "down")
    await hass.async_block_till_done()

    assert notify_calls == []


async def test_missing_notify_service_does_not_break_the_update(
    hass: HomeAssistant, aioclient_mock, init_integration: MockConfigEntry
) -> None:
    hass.config_entries.async_update_entry(
        init_integration,
        options={
            CONF_NOTIFY_SERVICE: "notify.does_not_exist",
            CONF_NOTIFY_ON: ["down"],
            CONF_NOTIFY_MONITORS: [],
        },
    )
    await hass.async_block_till_done()

    events = async_capture_events(hass, EVENT_MONITOR_STATUS_CHANGED)
    await _refresh_with(hass, aioclient_mock, init_integration, "down")
    await hass.async_block_till_done()

    # The event still fires; only the notification is skipped.
    assert len(events) == 1
    assert init_integration.runtime_data.coordinator.last_update_success

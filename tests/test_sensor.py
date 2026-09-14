"""Entity creation and state mapping."""

from __future__ import annotations

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import API, load


async def test_monitor_entities(hass, init_integration: MockConfigEntry) -> None:
    assert hass.states.get("sensor.website_status").state == "up"
    assert hass.states.get("binary_sensor.website_reachable").state == STATE_ON
    assert hass.states.get("binary_sensor.gateway_reachable").state == STATE_OFF


async def test_status_attributes_never_expose_raw_config(
    hass, init_integration: MockConfigEntry
) -> None:
    """`config` carries headers and credentials and must stay out of states."""
    attributes = hass.states.get("sensor.website_status").attributes

    assert attributes["target"] == "https://example.com"
    assert attributes["tags"] == ["prod"]
    assert "config" not in attributes
    assert "X-Secret" not in str(attributes)


async def test_ping_monitor_target(hass, init_integration: MockConfigEntry) -> None:
    assert hass.states.get("sensor.gateway_status").attributes["target"] == "10.0.0.1"


async def test_ssl_sensor_only_for_https_monitors(
    hass, init_integration: MockConfigEntry
) -> None:
    """A ping monitor can never produce ssl_info, so it gets no SSL entity."""
    registry = er.async_get(hass)

    assert registry.async_get("sensor.website_ssl_certificate_expires_in") is not None
    assert registry.async_get("sensor.gateway_ssl_certificate_expires_in") is None


async def test_detail_sensors(hass, init_integration: MockConfigEntry) -> None:
    detail = init_integration.runtime_data.detail_coordinator
    await detail.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("sensor.website_response_time").state == "142"
    assert hass.states.get("sensor.website_uptime_24_h").state == "99.5"
    assert hass.states.get("sensor.website_ssl_certificate_expires_in").state == "45"


async def test_longer_uptime_windows_are_disabled_by_default(
    hass, init_integration: MockConfigEntry
) -> None:
    registry = er.async_get(hass)

    assert registry.async_get("sensor.website_uptime_24_h").disabled_by is None
    assert registry.async_get("sensor.website_uptime_7_days").disabled_by is not None


async def test_dashboard_sensors(hass, init_integration: MockConfigEntry) -> None:
    total = hass.states.get("sensor.uptimesphere_uptime_example_com_monitors_total")
    down = hass.states.get("binary_sensor.uptimesphere_uptime_example_com_monitor_down")

    assert total.state == "3"
    assert down.state == STATE_ON


async def test_paused_monitor_is_unknown_not_offline(
    hass, aioclient_mock, init_integration: MockConfigEntry
) -> None:
    """Reporting paused as "off" would write fake downtime into history."""
    page1 = load("monitors_page1")
    page1["data"]["data"][0]["status"] = "paused"

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/dashboard", json=load("dashboard"))
    aioclient_mock.get(f"{API}/monitors", params={"page": "1"}, json=page1)
    aioclient_mock.get(
        f"{API}/monitors", params={"page": "2"}, json=load("monitors_page2")
    )

    await init_integration.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.website_reachable").state == STATE_UNKNOWN
    assert hass.states.get("sensor.website_status").state == "paused"


async def test_unknown_status_does_not_raise(
    hass, aioclient_mock, init_integration: MockConfigEntry
) -> None:
    """A status the backend adds later must not break the entity."""
    page1 = load("monitors_page1")
    page1["data"]["data"][0]["status"] = "quarantined"

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/dashboard", json=load("dashboard"))
    aioclient_mock.get(f"{API}/monitors", params={"page": "1"}, json=page1)
    aioclient_mock.get(
        f"{API}/monitors", params={"page": "2"}, json=load("monitors_page2")
    )

    await init_integration.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    # Outside the declared enum options, so both entities report unknown
    # rather than guessing or failing the state write.
    assert hass.states.get("sensor.website_status").state == STATE_UNKNOWN
    assert hass.states.get("binary_sensor.website_reachable").state == STATE_UNKNOWN

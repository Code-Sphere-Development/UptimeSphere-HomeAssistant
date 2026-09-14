"""Diagnostics redaction."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.uptimesphere.const import CONF_API_TOKEN

from .conftest import TOKEN


async def test_diagnostics_redacts_secrets(
    hass, hass_client, init_integration: MockConfigEntry
) -> None:
    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, init_integration
    )

    assert diagnostics["entry"]["data"][CONF_API_TOKEN] != TOKEN
    # Monitor configs carry custom headers and credentials.
    assert "X-Secret" not in str(diagnostics["monitors"])
    assert TOKEN not in str(diagnostics)


async def test_diagnostics_keeps_ssl_info(
    hass, hass_client, init_integration: MockConfigEntry
) -> None:
    """ssl_info holds no secrets and is the most useful part of a bug report."""
    detail = init_integration.runtime_data.detail_coordinator
    await detail.async_refresh()
    await hass.async_block_till_done()

    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, init_integration
    )

    assert diagnostics["detail"]["latest_result"]["1"]["ssl_info"]["issuer"] == (
        "Let's Encrypt"
    )

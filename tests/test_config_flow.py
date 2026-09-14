"""Tests for the UptimeSphere config and options flow."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.uptimesphere.const import (
    CONF_API_TOKEN,
    CONF_BASE_URL,
    CONF_NOTIFY_ON,
    CONF_NOTIFY_SERVICE,
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL,
    DOMAIN,
)

from .conftest import API, BASE_URL, TOKEN, load

USER_INPUT = {
    CONF_BASE_URL: BASE_URL,
    CONF_API_TOKEN: TOKEN,
    CONF_VERIFY_SSL: True,
}


async def test_user_flow_creates_entry(hass, mock_api) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "UptimeSphere (uptime.example.com)"
    assert result["data"] == USER_INPUT
    assert result["result"].unique_id == f"{BASE_URL}::7"


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (401, {"message": "Unauthenticated."}, "invalid_auth"),
        (
            403,
            {"message": "You do not have access to any team for this application."},
            "no_team",
        ),
    ],
)
async def test_user_flow_errors(
    hass, aioclient_mock, status: int, payload: dict, expected: str
) -> None:
    """A rejected token and a team-less account must be told apart."""
    if status == 401:
        aioclient_mock.get(f"{API}/user", status=401, json=payload)
    else:
        aioclient_mock.get(f"{API}/user", json=load("user"))
        aioclient_mock.get(f"{API}/dashboard", status=403, json=payload)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_user_flow_cannot_connect(hass, aioclient_mock) -> None:
    aioclient_mock.get(f"{API}/user", exc=TimeoutError)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_invalid_url(hass) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_BASE_URL: "ftp://nope"}
    )

    assert result["errors"] == {"base": "invalid_url"}


async def test_user_flow_recovers_after_error(hass, aioclient_mock) -> None:
    """A corrected token in the same flow must succeed."""
    aioclient_mock.get(f"{API}/user", status=401, json={"message": "Unauthenticated."})

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/user", json=load("user"))
    aioclient_mock.get(f"{API}/dashboard", json=load("dashboard"))

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_duplicate_account_aborts(
    hass, mock_api, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_token(
    hass, mock_api, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)

    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "fresh-token"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_API_TOKEN] == "fresh-token"


async def test_reauth_rejects_a_different_account(
    hass, aioclient_mock, mock_config_entry: MockConfigEntry
) -> None:
    """A token for another account would silently orphan every entity."""
    other = load("user")
    other["id"] = 99
    aioclient_mock.get(f"{API}/user", json=other)
    aioclient_mock.get(f"{API}/dashboard", json=load("dashboard"))

    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "someone-elses-token"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert mock_config_entry.data[CONF_API_TOKEN] == TOKEN


async def test_options_flow_persists(hass, init_integration) -> None:
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 120,
            "detail_scan_interval": 45,
            CONF_NOTIFY_SERVICE: "notify.persistent_notification",
            CONF_NOTIFY_ON: ["down"],
            "notify_monitors": ["1"],
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert init_integration.options[CONF_SCAN_INTERVAL] == 120
    assert init_integration.options[CONF_NOTIFY_ON] == ["down"]

"""Shared fixtures for the UptimeSphere tests."""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.uptimesphere.const import (
    CONF_API_TOKEN,
    CONF_BASE_URL,
    CONF_VERIFY_SSL,
    DOMAIN,
)

BASE_URL = "https://uptime.example.com"
API = f"{BASE_URL}/api/v1"
TOKEN = "test-token"

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load(name: str) -> Any:
    """Load a JSON fixture."""
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Custom integrations are not loaded in tests unless enabled."""
    yield


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A configured UptimeSphere entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="UptimeSphere (uptime.example.com)",
        unique_id=f"{BASE_URL}::7",
        data={
            CONF_BASE_URL: BASE_URL,
            CONF_API_TOKEN: TOKEN,
            CONF_VERIFY_SSL: True,
        },
        options={},
    )


@pytest.fixture
def mock_api(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Register every endpoint the integration uses, with realistic payloads."""
    aioclient_mock.get(f"{API}/user", json=load("user"))
    aioclient_mock.get(f"{API}/dashboard", json=load("dashboard"))
    aioclient_mock.get(
        f"{API}/monitors", params={"page": "1"}, json=load("monitors_page1")
    )
    aioclient_mock.get(
        f"{API}/monitors", params={"page": "2"}, json=load("monitors_page2")
    )

    for monitor_id in (1, 2):
        aioclient_mock.get(f"{API}/monitors/{monitor_id}/uptime", json=load("uptime"))
        aioclient_mock.get(f"{API}/monitors/{monitor_id}/history", json=load("history"))

    return aioclient_mock


@pytest.fixture
async def init_integration(
    hass, mock_config_entry: MockConfigEntry, mock_api: AiohttpClientMocker
) -> MockConfigEntry:
    """Set up the integration against the mocked API."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry

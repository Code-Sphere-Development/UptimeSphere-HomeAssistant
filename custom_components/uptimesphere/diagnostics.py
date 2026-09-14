"""Diagnostics for UptimeSphere."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import UptimeSphereConfigEntry
from .const import CONF_API_TOKEN

# Monitor `config` objects carry database, SMTP and basic-auth credentials as
# well as custom request headers, so they are redacted wholesale rather than
# key by key.
TO_REDACT = {
    CONF_API_TOKEN,
    "access_token",
    "refresh_token",
    "api_key",
    "auth",
    "authorization",
    "email",
    "headers",
    "password",
    "secret",
    "token",
    "username",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: UptimeSphereConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    detail = runtime.detail_coordinator

    snapshot = coordinator.data
    detail_data = detail.data

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_exception": str(coordinator.last_exception)
            if coordinator.last_exception
            else None,
            "update_interval": str(coordinator.update_interval),
        },
        "detail_coordinator": {
            "last_update_success": detail.last_update_success,
            "last_exception": str(detail.last_exception)
            if detail.last_exception
            else None,
            "update_interval": str(detail.update_interval),
            "registered_monitors": sorted(detail.registered_monitors),
        },
        "dashboard": snapshot.dashboard if snapshot else None,
        "monitors": [
            async_redact_data(monitor, TO_REDACT)
            for monitor in (snapshot.monitors.values() if snapshot else [])
        ],
        "detail": {
            "uptime": detail_data.uptime if detail_data else {},
            # ssl_info and dns_info are kept verbatim: they contain no secrets
            # and are the most useful thing in a bug report.
            "latest_result": detail_data.latest_result if detail_data else {},
        },
    }

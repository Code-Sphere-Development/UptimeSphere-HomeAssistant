"""The UptimeSphere integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import StaticTokenAuth, UptimeSphereClient
from .const import (
    CONF_API_TOKEN,
    CONF_BASE_URL,
    CONF_DETAIL_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL,
    DEFAULT_DETAIL_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ISSUE_NO_TEAM,
)
from .coordinator import MonitorCoordinator, MonitorDetailCoordinator
from .notify_dispatch import StatusChangeDispatcher

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


@dataclass(slots=True)
class UptimeSphereRuntimeData:
    """Everything the platforms need, hung off the config entry."""

    client: UptimeSphereClient
    coordinator: MonitorCoordinator
    detail_coordinator: MonitorDetailCoordinator


UptimeSphereConfigEntry = ConfigEntry[UptimeSphereRuntimeData]


async def async_setup_entry(
    hass: HomeAssistant, entry: UptimeSphereConfigEntry
) -> bool:
    """Set up UptimeSphere from a config entry."""
    session = async_get_clientsession(
        hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True)
    )
    client = UptimeSphereClient(
        session,
        entry.data[CONF_BASE_URL],
        StaticTokenAuth(entry.data[CONF_API_TOKEN]),
    )

    options = entry.options
    coordinator = MonitorCoordinator(
        hass,
        entry,
        client,
        timedelta(seconds=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
    )
    detail_coordinator = MonitorDetailCoordinator(
        hass,
        entry,
        client,
        timedelta(
            minutes=options.get(CONF_DETAIL_SCAN_INTERVAL, DEFAULT_DETAIL_SCAN_INTERVAL)
        ),
    )

    try:
        # Raises ConfigEntryNotReady / ConfigEntryAuthFailed for us.
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        # The "no team" 403 typically fails the very first refresh, before any
        # listener exists -- so the repair issue has to be raised here too.
        _async_sync_team_issue(hass, coordinator)
        raise

    # No first refresh for the detail coordinator: no entity has registered
    # yet, so it would fetch nothing.

    entry.runtime_data = UptimeSphereRuntimeData(
        client=client,
        coordinator=coordinator,
        detail_coordinator=detail_coordinator,
    )

    dispatcher = StatusChangeDispatcher(hass, entry, coordinator)
    entry.async_on_unload(dispatcher.async_start())
    entry.async_on_unload(_async_watch_team_issue(hass, coordinator))
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: UptimeSphereConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(
    hass: HomeAssistant, entry: UptimeSphereConfigEntry
) -> None:
    """Reload when the options change (intervals and notify settings)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: UptimeSphereConfigEntry, device_entry
) -> bool:
    """Allow deleting the device of a monitor that no longer exists."""
    snapshot = entry.runtime_data.coordinator.data
    if snapshot is None:
        return False

    known = {f"{entry.entry_id}_{monitor_id}" for monitor_id in snapshot.monitors}
    known.add(entry.entry_id)

    return not any(
        identifier in known
        for domain, identifier in device_entry.identifiers
        if domain == DOMAIN
    )


@callback
def _async_sync_team_issue(
    hass: HomeAssistant, coordinator: MonitorCoordinator
) -> None:
    """Surface the server's "no team" 403 as an actionable repair issue.

    That 403 is not an auth failure -- it also happens with a perfectly valid
    token once the CodeSphere refresh token stored server-side has expired,
    which no amount of re-entering the token will fix. The user has to sign in
    to the web UI once, so a reauth prompt would be actively misleading.
    """
    if coordinator.no_team_detected:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_NO_TEAM,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_NO_TEAM,
            translation_placeholders={"url": coordinator.client.base_url},
            learn_more_url=coordinator.client.base_url,
        )
    elif coordinator.last_update_success:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_NO_TEAM)


@callback
def _async_watch_team_issue(
    hass: HomeAssistant, coordinator: MonitorCoordinator
) -> CALLBACK_TYPE:
    """Keep the repair issue in sync with every later refresh."""

    @callback
    def _check() -> None:
        _async_sync_team_issue(hass, coordinator)

    _check()
    return coordinator.async_add_listener(_check)

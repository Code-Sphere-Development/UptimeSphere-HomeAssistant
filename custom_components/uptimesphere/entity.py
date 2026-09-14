"""Shared entity base classes and device grouping for UptimeSphere."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MonitorCoordinator, MonitorDetailCoordinator

MANUFACTURER = "Code-Sphere Development"


def hub_identifiers(entry: ConfigEntry) -> set[tuple[str, str]]:
    return {(DOMAIN, entry.entry_id)}


def hub_device_info(entry: ConfigEntry, base_url: str) -> DeviceInfo:
    """The instance-level device carrying the dashboard aggregates."""
    host = urlsplit(base_url).netloc or base_url
    return DeviceInfo(
        identifiers=hub_identifiers(entry),
        manufacturer=MANUFACTURER,
        name=f"UptimeSphere ({host})",
        model="UptimeSphere",
        configuration_url=base_url,
        entry_type=None,
    )


def monitor_device_info(
    entry: ConfigEntry, monitor: dict[str, Any], base_url: str
) -> DeviceInfo:
    """One device per monitor, hanging off the instance device."""
    monitor_id = monitor["id"]
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_{monitor_id}")},
        manufacturer=MANUFACTURER,
        name=str(monitor.get("name") or f"Monitor {monitor_id}"),
        model=str(monitor.get("type") or "monitor"),
        configuration_url=f"{base_url}/monitors/{monitor_id}",
        via_device=(DOMAIN, entry.entry_id),
    )


class UptimeSphereMonitorEntity(CoordinatorEntity[MonitorCoordinator]):
    """Base for entities fed by the monitor list."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MonitorCoordinator,
        entry: ConfigEntry,
        monitor: dict[str, Any],
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._monitor_id: int = monitor["id"]
        self._attr_unique_id = f"{entry.entry_id}_{self._monitor_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = monitor_device_info(
            entry, monitor, coordinator.client.base_url
        )

    @property
    def monitor(self) -> dict[str, Any] | None:
        """The monitor's current payload, or ``None`` if it disappeared."""
        snapshot = self.coordinator.data
        if snapshot is None:
            return None
        return snapshot.monitors.get(self._monitor_id)

    @property
    def available(self) -> bool:
        return super().available and self.monitor is not None


class UptimeSphereDetailEntity(CoordinatorEntity[MonitorDetailCoordinator]):
    """Base for entities fed by the (slow, opt-in) detail coordinator.

    Registers its monitor with the detail coordinator on add and unregisters on
    removal, so a disabled entity costs the server nothing.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        detail_coordinator: MonitorDetailCoordinator,
        monitor_coordinator: MonitorCoordinator,
        entry: ConfigEntry,
        monitor: dict[str, Any],
        key: str,
    ) -> None:
        super().__init__(detail_coordinator)
        self._entry = entry
        self._monitor_coordinator = monitor_coordinator
        self._monitor_id: int = monitor["id"]
        self._attr_unique_id = f"{entry.entry_id}_{self._monitor_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = monitor_device_info(
            entry, monitor, monitor_coordinator.client.base_url
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Unregisters automatically when the entity goes away, so a monitor
        # whose detail entities are all disabled costs the server nothing.
        self.async_on_remove(self.coordinator.register_monitor(self._monitor_id))

    @property
    def monitor(self) -> dict[str, Any] | None:
        snapshot = self._monitor_coordinator.data
        if snapshot is None:
            return None
        return snapshot.monitors.get(self._monitor_id)

    @property
    def available(self) -> bool:
        return super().available and self.monitor is not None


class UptimeSphereHubEntity(CoordinatorEntity[MonitorCoordinator]):
    """Base for the instance-level dashboard entities."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: MonitorCoordinator, entry: ConfigEntry, key: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_dashboard_{key}"
        self._attr_translation_key = key
        self._attr_device_info = hub_device_info(entry, coordinator.client.base_url)

    @property
    def dashboard(self) -> dict[str, Any]:
        snapshot = self.coordinator.data
        return {} if snapshot is None else snapshot.dashboard

"""Binary sensors for UptimeSphere."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import UptimeSphereConfigEntry
from .const import MONITOR_STATUSES, STATUSES_INDETERMINATE, STATUSES_ONLINE
from .entity import UptimeSphereHubEntity, UptimeSphereMonitorEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UptimeSphereConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensors, including ones for monitors added later."""
    coordinator = entry.runtime_data.coordinator
    known: set[int] = set()

    @callback
    def _async_add_new() -> None:
        snapshot = coordinator.data
        if snapshot is None:
            return

        new_ids = set(snapshot.monitors) - known
        if not new_ids:
            return

        known.update(new_ids)
        async_add_entities(
            MonitorReachableBinarySensor(coordinator, entry, snapshot.monitors[mid])
            for mid in sorted(new_ids)
        )

    async_add_entities([AnyMonitorDownBinarySensor(coordinator, entry)])
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new))
    _async_add_new()


class MonitorReachableBinarySensor(UptimeSphereMonitorEntity, BinarySensorEntity):
    """Whether the monitored target is currently reachable."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator, entry, monitor: dict[str, Any]) -> None:
        super().__init__(coordinator, entry, monitor, "reachable")

    @property
    def is_on(self) -> bool | None:
        monitor = self.monitor
        if monitor is None:
            return None

        status = monitor.get("status")

        # `paused`, `maintenance`, `pending` and `unknown` are not statements
        # about reachability. Reporting them as "off" would write fake downtime
        # into the recorder history. A status the backend adds later is treated
        # the same way rather than being guessed at.
        if status in STATUSES_INDETERMINATE or status not in MONITOR_STATUSES:
            return None

        return status in STATUSES_ONLINE


class AnyMonitorDownBinarySensor(UptimeSphereHubEntity, BinarySensorEntity):
    """On while at least one monitor of the instance is down."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "any_monitor_down")

    @property
    def is_on(self) -> bool | None:
        offline = self.dashboard.get("offline")
        if not isinstance(offline, int):
            return None
        return offline > 0

"""Sensors for UptimeSphere.

Three groups:

* monitor sensors fed by the fast monitor-list poll,
* monitor detail sensors fed by the slow, opt-in detail poll,
* instance-level aggregates from ``/dashboard``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import UptimeSphereConfigEntry
from .const import MONITOR_STATUSES, TYPE_HTTPS, UPTIME_PERIODS
from .coordinator import MonitorCoordinator, MonitorDetailCoordinator
from .entity import (
    UptimeSphereDetailEntity,
    UptimeSphereHubEntity,
    UptimeSphereMonitorEntity,
)
from .util import as_float, as_int, derive_target, parse_timestamp, tag_names

MILLISECONDS = "ms"


def _status_value(monitor: dict[str, Any]) -> str | None:
    """Report only statuses we declared as enum options.

    An enum sensor whose state is outside `options` fails the state write, so a
    status added to the backend later must degrade to unknown, not break.
    """
    status = monitor.get("status")
    return status if status in MONITOR_STATUSES else None


@dataclass(frozen=True, kw_only=True)
class MonitorSensorDescription(SensorEntityDescription):
    """Describes a sensor read straight off the monitor resource."""

    value_fn: Callable[[dict[str, Any]], Any]


@dataclass(frozen=True, kw_only=True)
class DashboardSensorDescription(SensorEntityDescription):
    """Describes a sensor read off the dashboard aggregates."""

    value_fn: Callable[[dict[str, Any]], Any]


MONITOR_SENSORS: tuple[MonitorSensorDescription, ...] = (
    MonitorSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=list(MONITOR_STATUSES),
        value_fn=_status_value,
    ),
    MonitorSensorDescription(
        key="last_checked",
        translation_key="last_checked",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda monitor: parse_timestamp(monitor.get("last_checked_at")),
    ),
    MonitorSensorDescription(
        key="next_check",
        translation_key="next_check",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda monitor: parse_timestamp(monitor.get("next_check_at")),
    ),
)

DASHBOARD_SENSORS: tuple[DashboardSensorDescription, ...] = (
    DashboardSensorDescription(
        key="monitors_total",
        translation_key="monitors_total",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: as_int(data.get("total")),
    ),
    DashboardSensorDescription(
        key="monitors_online",
        translation_key="monitors_online",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: as_int(data.get("online")),
    ),
    DashboardSensorDescription(
        key="monitors_offline",
        translation_key="monitors_offline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: as_int(data.get("offline")),
    ),
    DashboardSensorDescription(
        key="monitors_warning",
        translation_key="monitors_warning",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: as_int(data.get("warning")),
    ),
    DashboardSensorDescription(
        key="ssl_expiring_soon",
        translation_key="ssl_expiring_soon",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: as_int(data.get("ssl_expiring_soon")),
    ),
    DashboardSensorDescription(
        key="avg_response_time",
        translation_key="avg_response_time",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=MILLISECONDS,
        value_fn=lambda data: as_int(data.get("avg_response_time_ms")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UptimeSphereConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors, including ones for monitors added later."""
    coordinator = entry.runtime_data.coordinator
    detail_coordinator = entry.runtime_data.detail_coordinator
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

        entities: list[SensorEntity] = []
        for monitor_id in sorted(new_ids):
            monitor = snapshot.monitors[monitor_id]
            entities.extend(
                _build_monitor_sensors(coordinator, detail_coordinator, entry, monitor)
            )

        async_add_entities(entities)

    async_add_entities(
        DashboardSensor(coordinator, entry, description)
        for description in DASHBOARD_SENSORS
    )
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new))
    _async_add_new()


def _build_monitor_sensors(
    coordinator: MonitorCoordinator,
    detail_coordinator: MonitorDetailCoordinator,
    entry: UptimeSphereConfigEntry,
    monitor: dict[str, Any],
) -> list[SensorEntity]:
    entities: list[SensorEntity] = [
        MonitorSensor(coordinator, entry, monitor, description)
        for description in MONITOR_SENSORS
    ]

    entities.append(ResponseTimeSensor(detail_coordinator, coordinator, entry, monitor))
    entities.extend(
        UptimeSensor(detail_coordinator, coordinator, entry, monitor, period)
        for period in UPTIME_PERIODS
    )

    # Only HTTPS checks ever populate ssl_info, so creating the sensor for
    # anything else would guarantee a permanently unknown entity.
    if monitor.get("type") == TYPE_HTTPS:
        entities.append(
            SslExpirySensor(detail_coordinator, coordinator, entry, monitor)
        )

    return entities


class MonitorSensor(UptimeSphereMonitorEntity, SensorEntity):
    """A sensor read directly off the monitor resource."""

    entity_description: MonitorSensorDescription

    def __init__(
        self,
        coordinator: MonitorCoordinator,
        entry: UptimeSphereConfigEntry,
        monitor: dict[str, Any],
        description: MonitorSensorDescription,
    ) -> None:
        super().__init__(coordinator, entry, monitor, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        monitor = self.monitor
        if monitor is None:
            return None
        return self.entity_description.value_fn(monitor)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        # Only the status sensor carries the descriptive attributes, so the
        # same data is not duplicated across every entity of the device.
        if self.entity_description.key != "status":
            return None

        monitor = self.monitor
        if monitor is None:
            return None

        return {
            "monitor_id": monitor.get("id"),
            "monitor_type": monitor.get("type"),
            "location": monitor.get("location"),
            "interval_seconds": monitor.get("interval_seconds"),
            "tags": tag_names(monitor),
            "target": derive_target(monitor),
        }


class ResponseTimeSensor(UptimeSphereDetailEntity, SensorEntity):
    """Response time of the monitor's most recent check."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = MILLISECONDS

    def __init__(
        self,
        detail_coordinator: MonitorDetailCoordinator,
        coordinator: MonitorCoordinator,
        entry: UptimeSphereConfigEntry,
        monitor: dict[str, Any],
    ) -> None:
        super().__init__(
            detail_coordinator, coordinator, entry, monitor, "response_time"
        )

    @property
    def native_value(self) -> int | None:
        result = self._latest_result()
        if result is None:
            return None
        return as_int(result.get("response_time_ms"))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        result = self._latest_result()
        if result is None:
            return None

        return {
            "http_code": result.get("http_code"),
            "last_error": result.get("error"),
            "duration_ms": result.get("duration_ms"),
            "attempts_made": result.get("attempts_made"),
        }

    def _latest_result(self) -> dict[str, Any] | None:
        detail = self.coordinator.data
        if detail is None:
            return None
        return detail.latest_result.get(self._monitor_id)


class UptimeSensor(UptimeSphereDetailEntity, SensorEntity):
    """Uptime percentage over one of the server's fixed windows."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        detail_coordinator: MonitorDetailCoordinator,
        coordinator: MonitorCoordinator,
        entry: UptimeSphereConfigEntry,
        monitor: dict[str, Any],
        period: str,
    ) -> None:
        super().__init__(
            detail_coordinator, coordinator, entry, monitor, f"uptime_{period}"
        )
        self._period = period
        # 24h is the one people put on a dashboard; the longer windows are
        # available but off by default to avoid six sensors per monitor.
        self._attr_entity_registry_enabled_default = period == "24h"

    @property
    def native_value(self) -> float | None:
        detail = self.coordinator.data
        if detail is None:
            return None

        uptime = detail.uptime.get(self._monitor_id)
        if uptime is None:
            return None

        # The API returns null for a window with no results at all.
        return as_float(uptime.get(self._period))


class SslExpirySensor(UptimeSphereDetailEntity, SensorEntity):
    """Days until the monitored certificate expires (HTTPS monitors only)."""

    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        detail_coordinator: MonitorDetailCoordinator,
        coordinator: MonitorCoordinator,
        entry: UptimeSphereConfigEntry,
        monitor: dict[str, Any],
    ) -> None:
        super().__init__(
            detail_coordinator, coordinator, entry, monitor, "ssl_expiry_days"
        )

    @property
    def native_value(self) -> int | None:
        return as_int(self._ssl_info().get("expires_in_days"))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        ssl_info = self._ssl_info()
        if not ssl_info:
            return None

        expires_at: datetime | None = parse_timestamp(ssl_info.get("expires_at"))
        return {
            "issuer": ssl_info.get("issuer"),
            "expires_at": expires_at.isoformat() if expires_at else None,
        }

    def _ssl_info(self) -> dict[str, Any]:
        detail = self.coordinator.data
        if detail is None:
            return {}

        result = detail.latest_result.get(self._monitor_id)
        if result is None:
            return {}

        # Only checks that actually completed a TLS handshake carry ssl_info;
        # a failed check stores null there.
        ssl_info = result.get("ssl_info")
        return ssl_info if isinstance(ssl_info, dict) else {}


class DashboardSensor(UptimeSphereHubEntity, SensorEntity):
    """An instance-level aggregate from /dashboard."""

    entity_description: DashboardSensorDescription

    def __init__(
        self,
        coordinator: MonitorCoordinator,
        entry: UptimeSphereConfigEntry,
        description: DashboardSensorDescription,
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.dashboard)

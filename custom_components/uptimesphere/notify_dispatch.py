"""Turns monitor status transitions into Home Assistant notifications.

Exactly one dispatcher per config entry listens on the monitor coordinator.
Detecting transitions in the entities instead would fire once per entity, and
would miss changes entirely whenever the user disables an entity.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback

from .const import (
    CONF_NOTIFY_MONITORS,
    CONF_NOTIFY_ON,
    CONF_NOTIFY_SERVICE,
    DEFAULT_NOTIFY_ON,
    EVENT_MONITOR_STATUS_CHANGED,
    STATUS_PENDING,
    STATUS_UNKNOWN,
)
from .coordinator import MonitorCoordinator

_LOGGER = logging.getLogger(__name__)

# `pending` and `unknown` are bookkeeping states the backend passes through on
# every retry cycle. Reporting them would turn a single outage into four
# notifications.
TRANSIENT_STATUSES = frozenset({STATUS_PENDING, STATUS_UNKNOWN})


class StatusChangeDispatcher:
    """Fires an event -- and optionally a notify service -- on status changes."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: MonitorCoordinator,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._coordinator = coordinator
        self._last_status: dict[int, str] = {}

    @callback
    def async_start(self) -> CALLBACK_TYPE:
        """Seed the baseline, then subscribe. Returns the unsubscribe callback.

        Seeding here rather than on the first listener call means a restart can
        never produce a burst of "changed" notifications: by the time the
        listener runs for the first time, a baseline already exists.
        """
        self._last_status = self._current_statuses()
        return self._coordinator.async_add_listener(self._handle_update)

    def _current_statuses(self) -> dict[int, str]:
        snapshot = self._coordinator.data
        if snapshot is None:
            return {}

        return {
            monitor_id: str(monitor["status"])
            for monitor_id, monitor in snapshot.monitors.items()
            if monitor.get("status") is not None
        }

    @callback
    def _handle_update(self) -> None:
        snapshot = self._coordinator.data
        if snapshot is None:
            return

        current = self._current_statuses()

        for monitor_id, new_status in current.items():
            old_status = self._last_status.get(monitor_id)

            # A monitor we have never seen before has not "changed".
            if old_status is None or old_status == new_status:
                continue

            if new_status in TRANSIENT_STATUSES or old_status in TRANSIENT_STATUSES:
                continue

            self._async_dispatch(snapshot.monitors[monitor_id], old_status, new_status)

        self._last_status = current

    @callback
    def _async_dispatch(
        self, monitor: dict[str, Any], old_status: str, new_status: str
    ) -> None:
        monitor_id = monitor.get("id")
        name = str(monitor.get("name") or f"Monitor {monitor_id}")

        self._hass.bus.async_fire(
            EVENT_MONITOR_STATUS_CHANGED,
            {
                "entry_id": self._entry.entry_id,
                "monitor_id": monitor_id,
                "name": name,
                "type": monitor.get("type"),
                "old_status": old_status,
                "new_status": new_status,
                "last_checked_at": monitor.get("last_checked_at"),
            },
        )

        service = self._entry.options.get(CONF_NOTIFY_SERVICE)
        if not service:
            return

        if new_status not in self._entry.options.get(CONF_NOTIFY_ON, DEFAULT_NOTIFY_ON):
            return

        selected = self._entry.options.get(CONF_NOTIFY_MONITORS) or []
        if selected and str(monitor_id) not in {str(item) for item in selected}:
            return

        self._hass.async_create_task(
            self._async_call_notify(service, name, old_status, new_status)
        )

    async def _async_call_notify(
        self, service: str, name: str, old_status: str, new_status: str
    ) -> None:
        # Stored fully qualified ("notify.mobile_app_x"), so a script or any
        # other service-shaped target works just as well as a notifier.
        domain, _, service_name = service.partition(".")
        if not service_name:
            domain, service_name = "notify", service

        if not self._hass.services.has_service(domain, service_name):
            _LOGGER.warning(
                "Configured notification service %s.%s does not exist; "
                "skipping notification for %s",
                domain,
                service_name,
                name,
            )
            return

        try:
            await self._hass.services.async_call(
                domain,
                service_name,
                {
                    "title": f"UptimeSphere: {name}",
                    "message": f"{name}: {old_status} → {new_status}",
                },
                blocking=False,
            )
        except Exception as err:
            # take the coordinator update down with it.
            _LOGGER.error(
                "Calling %s.%s for %s failed: %s", domain, service_name, name, err
            )

"""Data coordinators for UptimeSphere.

Two coordinators, because the two kinds of data have wildly different costs:

``MonitorCoordinator``
    Two cheap requests (`/monitors`, `/dashboard`) on a short interval.

``MonitorDetailCoordinator``
    Two requests *per monitor* (`/uptime`, `/history?limit=1`) on a long
    interval, and only for monitors that actually have an enabled entity.
    `/uptime` alone runs ten COUNT queries server-side, and every request to
    any `/api/v1` route makes UptimeSphere call CodeSphere Accounts
    synchronously -- so these must stay rare.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    UptimeSphereApiError,
    UptimeSphereAuthError,
    UptimeSphereClient,
    UptimeSphereConnectionError,
    UptimeSphereNoTeamError,
)
from .const import DETAIL_CONCURRENCY, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Wait this long after an entity registers before refetching detail data, so
# that adding twenty entities at startup results in one refresh, not twenty.
REGISTRATION_DEBOUNCE = 5.0


@dataclass(slots=True)
class MonitorSnapshot:
    """Result of one :class:`MonitorCoordinator` refresh."""

    monitors: dict[int, dict[str, Any]]
    dashboard: dict[str, Any]


@dataclass(slots=True)
class MonitorDetail:
    """Result of one :class:`MonitorDetailCoordinator` refresh."""

    uptime: dict[int, dict[str, Any]]
    latest_result: dict[int, dict[str, Any] | None]


class UptimeSphereCoordinatorMixin:
    """Shared translation of API errors into coordinator outcomes."""

    # Set while the server answers 403 "no accessible team". Exposed as state
    # rather than matched out of an error message, because __init__.py turns it
    # into a repair issue and that must not depend on wording.
    no_team_detected: bool = False

    def _translate(self, err: Exception) -> Exception:
        self.no_team_detected = isinstance(err, UptimeSphereNoTeamError)

        if isinstance(err, UptimeSphereAuthError):
            # Triggers the reauth flow rather than an endless retry loop.
            return ConfigEntryAuthFailed("API token rejected")

        if isinstance(err, UptimeSphereNoTeamError):
            # Deliberately NOT ConfigEntryAuthFailed: the token is fine, the
            # server-side CodeSphere session is not. __init__.py raises a
            # repair issue for this case.
            return UpdateFailed(
                "UptimeSphere reports no accessible team for this account"
            )

        if isinstance(err, (UptimeSphereConnectionError, UptimeSphereApiError)):
            return UpdateFailed(str(err))

        return err


class MonitorCoordinator(
    UptimeSphereCoordinatorMixin, DataUpdateCoordinator[MonitorSnapshot]
):
    """Polls the monitor list and the dashboard aggregates."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: UptimeSphereClient,
        interval: timedelta,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} monitors",
            update_interval=interval,
            config_entry=entry,
        )
        self.client = client

    async def _async_update_data(self) -> MonitorSnapshot:
        try:
            monitors, dashboard = await asyncio.gather(
                self.client.async_get_monitors(),
                self.client.async_get_dashboard(),
            )
        except Exception as err:
            raise self._translate(err) from err

        self.no_team_detected = False

        by_id: dict[int, dict[str, Any]] = {}
        for monitor in monitors:
            monitor_id = monitor.get("id")
            if isinstance(monitor_id, int):
                by_id[monitor_id] = monitor
            else:
                _LOGGER.debug("Skipping monitor without a usable id: %s", monitor)

        return MonitorSnapshot(monitors=by_id, dashboard=dashboard)


class MonitorDetailCoordinator(
    UptimeSphereCoordinatorMixin, DataUpdateCoordinator[MonitorDetail]
):
    """Polls uptime percentages and the latest result for registered monitors.

    Entities register themselves when they are added to hass and unregister
    when removed, so a monitor whose detail entities are all disabled costs
    nothing.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: UptimeSphereClient,
        interval: timedelta,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} monitor details",
            update_interval=interval,
            config_entry=entry,
        )
        self.client = client
        # Reference counted: a monitor has several detail-backed entities, and
        # removing one of them must not stop fetching for the others.
        self._registered: Counter[int] = Counter()
        self._semaphore = asyncio.Semaphore(DETAIL_CONCURRENCY)
        self._registration_debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=REGISTRATION_DEBOUNCE,
            immediate=False,
            function=self.async_request_refresh,
        )

    @callback
    def register_monitor(self, monitor_id: int) -> CALLBACK_TYPE:
        """Start fetching detail data for ``monitor_id``.

        Returns the matching unregister callback, so callers can hand it
        straight to ``Entity.async_on_remove``.
        """
        first = self._registered[monitor_id] == 0
        self._registered[monitor_id] += 1

        if first:
            # Debounced: twenty entities registering at startup produce one
            # refresh, not twenty.
            self.hass.async_create_task(self._registration_debouncer.async_call())

        return partial(self._unregister_monitor, monitor_id)

    @callback
    def _unregister_monitor(self, monitor_id: int) -> None:
        if self._registered[monitor_id] <= 1:
            del self._registered[monitor_id]
        else:
            self._registered[monitor_id] -= 1

    @property
    def registered_monitors(self) -> frozenset[int]:
        return frozenset(self._registered)

    async def _async_update_data(self) -> MonitorDetail:
        monitor_ids = sorted(self._registered)  # Counter iterates its keys
        if not monitor_ids:
            return MonitorDetail(uptime={}, latest_result={})

        previous = self.data
        uptime: dict[int, dict[str, Any]] = {}
        latest: dict[int, dict[str, Any] | None] = {}
        fatal: Exception | None = None

        async def fetch(monitor_id: int) -> None:
            nonlocal fatal
            async with self._semaphore:
                try:
                    uptime[monitor_id], latest[monitor_id] = await asyncio.gather(
                        self.client.async_get_uptime(monitor_id),
                        self.client.async_get_latest_result(monitor_id),
                    )
                except (UptimeSphereAuthError, UptimeSphereNoTeamError) as err:
                    # Applies to the whole entry, not just this monitor.
                    fatal = err
                except (UptimeSphereConnectionError, UptimeSphereApiError) as err:
                    # One monitor failing must not blank out all the others;
                    # carry its previous values forward instead.
                    _LOGGER.debug(
                        "Detail fetch failed for monitor %s: %s", monitor_id, err
                    )
                    if previous is not None:
                        if monitor_id in previous.uptime:
                            uptime[monitor_id] = previous.uptime[monitor_id]
                        if monitor_id in previous.latest_result:
                            latest[monitor_id] = previous.latest_result[monitor_id]

        await asyncio.gather(*(fetch(monitor_id) for monitor_id in monitor_ids))

        if fatal is not None:
            raise self._translate(fatal) from fatal

        if not uptime and not latest:
            raise UpdateFailed("No detail data could be fetched for any monitor")

        return MonitorDetail(uptime=uptime, latest_result=latest)

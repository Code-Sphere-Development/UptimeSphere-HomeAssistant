"""Config and options flow for UptimeSphere."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any
from urllib.parse import urlsplit

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import (
    StaticTokenAuth,
    UptimeSphereAuthError,
    UptimeSphereClient,
    UptimeSphereConnectionError,
    UptimeSphereNoTeamError,
    UptimeSphereParseError,
    normalize_base_url,
)
from .const import (
    CONF_API_TOKEN,
    CONF_BASE_URL,
    CONF_DETAIL_SCAN_INTERVAL,
    CONF_NOTIFY_MONITORS,
    CONF_NOTIFY_ON,
    CONF_NOTIFY_SERVICE,
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL,
    DEFAULT_DETAIL_SCAN_INTERVAL,
    DEFAULT_NOTIFY_ON,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_DETAIL_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_DETAIL_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    NOTIFIABLE_STATUSES,
)

_LOGGER = logging.getLogger(__name__)

NOTIFY_DOMAIN = "notify"


def _user_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_BASE_URL, default=defaults.get(CONF_BASE_URL, "")
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
            vol.Required(CONF_API_TOKEN): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
            vol.Required(
                CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, True)
            ): BooleanSelector(),
        }
    )


class UptimeSphereConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UptimeSphere config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the server URL and a personal access token.

        Phase 2 turns this into a menu offering the AccountSphere device flow
        as a second option; nothing else in the flow has to change.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url, user, error = await self._async_validate(user_input)

            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(f"{base_url}::{user['id']}")
                self._abort_if_unique_id_configured()

                host = urlsplit(base_url).netloc or base_url
                return self.async_create_entry(
                    title=f"UptimeSphere ({host})",
                    data={
                        CONF_BASE_URL: base_url,
                        CONF_API_TOKEN: user_input[CONF_API_TOKEN],
                        CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start re-authentication after the token was rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a replacement token; the URL stays fixed."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            candidate = {
                CONF_BASE_URL: entry.data[CONF_BASE_URL],
                CONF_API_TOKEN: user_input[CONF_API_TOKEN],
                CONF_VERIFY_SSL: entry.data.get(CONF_VERIFY_SSL, True),
            }
            base_url, user, error = await self._async_validate(candidate)

            if error:
                errors["base"] = error
            else:
                # Refuse a token belonging to a different account -- it would
                # silently repoint the entry and orphan every entity.
                await self.async_set_unique_id(f"{base_url}::{user['id']}")
                self._abort_if_unique_id_mismatch(reason="wrong_account")

                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_TOKEN: user_input[CONF_API_TOKEN]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            description_placeholders={"url": entry.data[CONF_BASE_URL]},
            errors=errors,
        )

    async def _async_validate(
        self, user_input: Mapping[str, Any]
    ) -> tuple[str, dict[str, Any], str | None]:
        """Probe the server. Returns ``(base_url, user, error_key)``."""
        try:
            base_url = normalize_base_url(user_input[CONF_BASE_URL])
        except ValueError:
            return "", {}, "invalid_url"

        session = async_get_clientsession(
            self.hass, verify_ssl=user_input.get(CONF_VERIFY_SSL, True)
        )
        client = UptimeSphereClient(
            session, base_url, StaticTokenAuth(user_input[CONF_API_TOKEN])
        )

        try:
            # /user works even for an account with no team, which is what makes
            # it possible to tell a bad token apart from a team problem.
            user = await client.async_get_user()
            await client.async_get_dashboard()
        except UptimeSphereAuthError:
            return base_url, {}, "invalid_auth"
        except UptimeSphereNoTeamError:
            return base_url, {}, "no_team"
        except UptimeSphereParseError:
            return base_url, {}, "invalid_host"
        except UptimeSphereConnectionError:
            return base_url, {}, "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error validating UptimeSphere")
            return base_url, {}, "unknown"

        if not isinstance(user.get("id"), int):
            return base_url, {}, "invalid_host"

        return base_url, user, None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> UptimeSphereOptionsFlow:
        return UptimeSphereOptionsFlow()


class UptimeSphereOptionsFlow(OptionsFlow):
    """Polling intervals and notification behaviour."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_DETAIL_SCAN_INTERVAL: int(
                        user_input[CONF_DETAIL_SCAN_INTERVAL]
                    ),
                    CONF_NOTIFY_SERVICE: user_input.get(CONF_NOTIFY_SERVICE, ""),
                    CONF_NOTIFY_ON: user_input.get(CONF_NOTIFY_ON, []),
                    CONF_NOTIFY_MONITORS: user_input.get(CONF_NOTIFY_MONITORS, []),
                }
            )

        return self.async_show_form(step_id="init", data_schema=self._schema())

    def _schema(self) -> vol.Schema:
        options = self.config_entry.options

        return vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=10,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_DETAIL_SCAN_INTERVAL,
                    default=options.get(
                        CONF_DETAIL_SCAN_INTERVAL, DEFAULT_DETAIL_SCAN_INTERVAL
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_DETAIL_SCAN_INTERVAL,
                        max=MAX_DETAIL_SCAN_INTERVAL,
                        step=5,
                        unit_of_measurement="min",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_NOTIFY_SERVICE,
                    default=options.get(CONF_NOTIFY_SERVICE, ""),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=self._notify_services(),
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                    )
                ),
                vol.Optional(
                    CONF_NOTIFY_ON,
                    default=options.get(CONF_NOTIFY_ON, DEFAULT_NOTIFY_ON),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=list(NOTIFIABLE_STATUSES),
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                        translation_key="monitor_status",
                    )
                ),
                vol.Optional(
                    CONF_NOTIFY_MONITORS,
                    default=options.get(CONF_NOTIFY_MONITORS, []),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=self._monitor_options(),
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

    def _notify_services(self) -> list[SelectOptionDict]:
        """Offer every registered notify service, plus an explicit "off"."""
        services = self.hass.services.async_services().get(NOTIFY_DOMAIN, {})
        return [SelectOptionDict(value="", label="—")] + [
            SelectOptionDict(
                value=f"{NOTIFY_DOMAIN}.{name}", label=f"{NOTIFY_DOMAIN}.{name}"
            )
            for name in sorted(services)
        ]

    def _monitor_options(self) -> list[SelectOptionDict]:
        """All known monitors; an empty selection means "every monitor"."""
        runtime = getattr(self.config_entry, "runtime_data", None)
        snapshot = runtime.coordinator.data if runtime else None
        if snapshot is None:
            return []

        return [
            SelectOptionDict(
                value=str(monitor_id),
                label=str(monitor.get("name") or f"Monitor {monitor_id}"),
            )
            for monitor_id, monitor in sorted(snapshot.monitors.items())
        ]

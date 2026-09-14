"""HTTP client for the UptimeSphere REST API (`/api/v1`).

The API has three different response envelopes, which this module is the only
place in the integration that has to know about:

* list endpoints are *double* wrapped -- ``{"data": {"data": [...], "meta": ...}}``
* single-resource endpoints return the bare resource, unwrapped
* ``/user``, ``/dashboard`` and ``/uptime`` return flat objects

Authentication is kept behind :class:`AuthStrategy` so that the planned
AccountSphere device-flow can be added as a second implementation without
touching the client.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import logging
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientError, ClientResponse, ClientSession, ClientTimeout

from .const import REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)

API_PREFIX = "/api/v1"

# The exact message EnsureUserHasCurrentTeam returns. Matched loosely (see
# _is_no_team) because a 403 from anywhere else must not be mistaken for it.
NO_TEAM_MESSAGE = "You do not have access to any team for this application."

MONITORS_PAGE_SIZE = 20
# Backstop against a server that keeps advertising further pages.
MAX_PAGES = 100


class UptimeSphereError(Exception):
    """Base class for every error raised by this client."""


class UptimeSphereConnectionError(UptimeSphereError):
    """The server could not be reached, or did not answer in time."""


class UptimeSphereAuthError(UptimeSphereError):
    """The token was rejected (HTTP 401). Re-authentication is required."""


class UptimeSphereNoTeamError(UptimeSphereError):
    """Authenticated, but the account currently resolves to no team (HTTP 403).

    This is not an authentication problem: it also happens with a perfectly
    valid API token once the CodeSphere refresh token stored server-side has
    expired because the user stopped logging into the web UI.
    """


class UptimeSphereApiError(UptimeSphereError):
    """The server answered, but with an unusable response."""


class UptimeSphereParseError(UptimeSphereApiError):
    """The response was not the JSON this API is supposed to return.

    Almost always means the URL points at something that is not UptimeSphere
    (a reverse proxy login page, the wrong host), so the config flow reports it
    separately from a plain connection failure.
    """


def normalize_base_url(raw: str) -> str:
    """Reduce a user-entered URL to ``scheme://host[:port]``.

    Accepts input with a trailing slash, a path, or a pasted ``/api/v1`` suffix.
    """
    candidate = raw.strip()
    if not candidate:
        raise ValueError("empty base url")

    if "://" not in candidate:
        candidate = f"https://{candidate}"

    parts = urlsplit(candidate)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"unsupported base url: {raw!r}")

    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


class AuthStrategy(ABC):
    """Supplies the ``Authorization`` header for every request."""

    @abstractmethod
    async def async_get_headers(self) -> dict[str, str]:
        """Return the auth headers to send with a request."""

    @abstractmethod
    async def async_refresh(self) -> bool:
        """Try to obtain fresh credentials.

        Returns ``True`` when the caller may retry the request, ``False`` when
        the user has to re-authenticate interactively.
        """


class StaticTokenAuth(AuthStrategy):
    """A Sanctum personal access token pasted by the user.

    Sanctum tokens created in the UptimeSphere web UI never expire, so there is
    nothing to refresh -- a 401 always means the token was revoked.

    `POST /api/v1/auth/refresh` is deliberately not used: it *deletes* the
    current token and returns a replacement, so a failure to persist the new
    value would lock the integration out permanently.
    """

    def __init__(self, token: str) -> None:
        self._token = token

    @property
    def token(self) -> str:
        return self._token

    async def async_get_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def async_refresh(self) -> bool:
        return False


class UptimeSphereClient:
    """Thin async wrapper around the UptimeSphere REST API."""

    def __init__(
        self,
        session: ClientSession,
        base_url: str,
        auth: AuthStrategy,
    ) -> None:
        # TLS verification is a property of the session: callers obtain it via
        # async_get_clientsession(hass, verify_ssl=...), which hands back HA's
        # separate cached unverified session when asked.
        self._session = session
        self._base_url = normalize_base_url(base_url)
        self._auth = auth
        self._timeout = ClientTimeout(total=REQUEST_TIMEOUT.total_seconds())

    @property
    def base_url(self) -> str:
        return self._base_url

    # -- public API ---------------------------------------------------------

    async def async_get_user(self) -> dict[str, Any]:
        """`GET /user` -- flat ``{id, name, email}``.

        Works even for an account with no team, which makes it the right probe
        for telling "bad token" apart from "no team".
        """
        return await self._get_json("/user")

    async def async_get_dashboard(self) -> dict[str, Any]:
        """`GET /dashboard` -- flat aggregate counters."""
        return await self._get_json("/dashboard")

    async def async_get_monitors(self) -> list[dict[str, Any]]:
        """`GET /monitors` -- walks every page (fixed 20 per page)."""
        monitors: list[dict[str, Any]] = []
        page = 1

        while page <= MAX_PAGES:
            payload = await self._get_json("/monitors", params={"page": page})
            body = self._unwrap_list(payload)
            monitors.extend(body["items"])

            last_page = body["last_page"]
            if last_page is not None:
                if page >= last_page:
                    break
            elif len(body["items"]) < MONITORS_PAGE_SIZE:
                # No usable meta -- stop on the first short page.
                break

            page += 1
        else:
            _LOGGER.warning(
                "Stopped paginating monitors after %s pages; the server kept "
                "advertising more results",
                MAX_PAGES,
            )

        return monitors

    async def async_get_uptime(self, monitor_id: int) -> dict[str, Any]:
        """`GET /monitors/{id}/uptime` -- flat ``{"24h": float|None, ...}``.

        Expensive server-side (ten COUNT queries per call), so callers must not
        put this on a short interval.
        """
        return await self._get_json(f"/monitors/{monitor_id}/uptime")

    async def async_get_latest_result(self, monitor_id: int) -> dict[str, Any] | None:
        """Most recent row of `GET /monitors/{id}/history`, or ``None``.

        The monitor resource carries no response time, so this is the only way
        to obtain one.
        """
        payload = await self._get_json(
            f"/monitors/{monitor_id}/history", params={"limit": 1}
        )
        items = self._unwrap_list(payload)["items"]
        return items[0] if items else None

    # -- internals ----------------------------------------------------------

    async def _get_json(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Perform a GET, retrying once if the auth strategy can refresh."""
        try:
            return await self._request(path, params)
        except UptimeSphereAuthError:
            if not await self._auth.async_refresh():
                raise
            return await self._request(path, params)

    async def _request(
        self, path: str, params: dict[str, Any] | None
    ) -> dict[str, Any]:
        url = f"{self._base_url}{API_PREFIX}{path}"
        headers = {"Accept": "application/json", **await self._auth.async_get_headers()}

        try:
            async with self._session.get(
                url,
                params=params,
                headers=headers,
                timeout=self._timeout,
            ) as response:
                await self._raise_for_status(response)

                try:
                    payload = await response.json(content_type=None)
                except ValueError as err:
                    raise UptimeSphereParseError(f"{path} did not return JSON") from err
        except TimeoutError as err:
            raise UptimeSphereConnectionError(f"{url} timed out") from err
        except ClientError as err:
            raise UptimeSphereConnectionError(f"{url} unreachable: {err}") from err

        if not isinstance(payload, dict):
            raise UptimeSphereParseError(
                f"{path} returned {type(payload).__name__}, expected an object"
            )

        return payload

    async def _raise_for_status(self, response: ClientResponse) -> None:
        if response.status < 400:
            return

        if response.status == 401:
            raise UptimeSphereAuthError("API token rejected")

        if response.status == 403 and await self._is_no_team(response):
            raise UptimeSphereNoTeamError(NO_TEAM_MESSAGE)

        raise UptimeSphereApiError(f"{response.url} returned HTTP {response.status}")

    @staticmethod
    async def _is_no_team(response: ClientResponse) -> bool:
        """Distinguish the team guard's 403 from any other forbidden response."""
        try:
            body = await response.json(content_type=None)
        except (ValueError, ClientError):
            return False

        if not isinstance(body, dict):
            return False

        return "do not have access to any team" in str(body.get("message", ""))

    @staticmethod
    def _unwrap_list(payload: dict[str, Any]) -> dict[str, Any]:
        """Flatten the double ``data`` envelope used by paginated endpoints."""
        inner = payload.get("data")
        if not isinstance(inner, dict):
            raise UptimeSphereParseError("expected a paginated object under 'data'")

        items = inner.get("data")
        if not isinstance(items, list):
            raise UptimeSphereParseError("expected a list under 'data.data'")

        meta = inner.get("meta")
        last_page = meta.get("last_page") if isinstance(meta, dict) else None

        return {
            "items": [item for item in items if isinstance(item, dict)],
            "last_page": last_page if isinstance(last_page, int) else None,
        }

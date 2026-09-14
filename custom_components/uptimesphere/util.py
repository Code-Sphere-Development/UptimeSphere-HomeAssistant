"""Small helpers shared by the UptimeSphere platforms."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util

from .const import TYPES_HOST_PORT, TYPES_URL


def derive_target(monitor: dict[str, Any]) -> str | None:
    """Render the monitored target as a short string.

    The raw ``config`` object is deliberately never exposed as a state
    attribute: it carries custom headers, basic-auth usernames and redacted
    password sentinels. Only the part that identifies *what* is monitored is
    surfaced.
    """
    config = monitor.get("config")
    if not isinstance(config, dict):
        return None

    monitor_type = monitor.get("type")

    if monitor_type in TYPES_URL:
        url = config.get("url")
        return str(url) if url else None

    host = config.get("host")
    if not host:
        return None

    if monitor_type in TYPES_HOST_PORT:
        port = config.get("port")
        return f"{host}:{port}" if port else str(host)

    return str(host)


def parse_timestamp(value: Any) -> datetime | None:
    """Parse a Laravel ISO-8601 timestamp (microsecond precision) safely."""
    if not isinstance(value, str) or not value:
        return None
    return dt_util.parse_datetime(value)


def as_float(value: Any) -> float | None:
    """Coerce an API number to float, treating anything unusable as missing."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def as_int(value: Any) -> int | None:
    """Coerce an API number to int, treating anything unusable as missing."""
    number = as_float(value)
    return None if number is None else int(number)


def tag_names(monitor: dict[str, Any]) -> list[str]:
    """Names of the monitor's tags, ignoring malformed entries."""
    tags = monitor.get("tags")
    if not isinstance(tags, list):
        return []

    return [
        str(tag["name"])
        for tag in tags
        if isinstance(tag, dict) and tag.get("name") is not None
    ]

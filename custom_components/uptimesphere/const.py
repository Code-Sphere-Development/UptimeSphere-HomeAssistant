"""Constants for the UptimeSphere integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "uptimesphere"

# --- Config entry keys -----------------------------------------------------

CONF_BASE_URL: Final = "base_url"
CONF_API_TOKEN: Final = "api_token"
CONF_VERIFY_SSL: Final = "verify_ssl"

# --- Options keys ----------------------------------------------------------

CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_DETAIL_SCAN_INTERVAL: Final = "detail_scan_interval"
CONF_NOTIFY_SERVICE: Final = "notify_service"
CONF_NOTIFY_ON: Final = "notify_on"
CONF_NOTIFY_MONITORS: Final = "notify_monitors"

DEFAULT_SCAN_INTERVAL: Final = 60  # seconds
MIN_SCAN_INTERVAL: Final = 30
MAX_SCAN_INTERVAL: Final = 3600

DEFAULT_DETAIL_SCAN_INTERVAL: Final = 30  # minutes
MIN_DETAIL_SCAN_INTERVAL: Final = 5
MAX_DETAIL_SCAN_INTERVAL: Final = 360

# The detail coordinator issues two requests per registered monitor and every
# request costs UptimeSphere an outbound call to CodeSphere Accounts, so keep
# the in-flight count low even on large installations.
DETAIL_CONCURRENCY: Final = 4

REQUEST_TIMEOUT: Final = timedelta(seconds=15)

# --- Events ----------------------------------------------------------------

EVENT_MONITOR_STATUS_CHANGED: Final = f"{DOMAIN}_monitor_status_changed"

# --- Monitor domain values (mirrors App\Enums in the Laravel backend) -------

STATUS_UNKNOWN: Final = "unknown"
STATUS_PENDING: Final = "pending"
STATUS_UP: Final = "up"
STATUS_DOWN: Final = "down"
STATUS_WARNING: Final = "warning"
STATUS_PAUSED: Final = "paused"
STATUS_MAINTENANCE: Final = "maintenance"

MONITOR_STATUSES: Final[list[str]] = [
    STATUS_UNKNOWN,
    STATUS_PENDING,
    STATUS_UP,
    STATUS_DOWN,
    STATUS_WARNING,
    STATUS_PAUSED,
    STATUS_MAINTENANCE,
]

# Statuses that can be the target of a notification. `unknown` and `pending`
# are transient bookkeeping states and would only produce noise.
NOTIFIABLE_STATUSES: Final[list[str]] = [
    STATUS_DOWN,
    STATUS_UP,
    STATUS_WARNING,
    STATUS_PAUSED,
    STATUS_MAINTENANCE,
]

DEFAULT_NOTIFY_ON: Final[list[str]] = [STATUS_DOWN, STATUS_UP]

# Statuses in which the monitor is considered reachable.
STATUSES_ONLINE: Final = frozenset({STATUS_UP, STATUS_WARNING})
# Statuses in which reachability is genuinely unknown rather than False.
STATUSES_INDETERMINATE: Final = frozenset(
    {STATUS_UNKNOWN, STATUS_PENDING, STATUS_PAUSED, STATUS_MAINTENANCE}
)

TYPE_HTTP: Final = "http"
TYPE_HTTPS: Final = "https"
TYPE_TCP: Final = "tcp"
TYPE_PING: Final = "ping"
TYPE_DNS: Final = "dns"
TYPE_DOCKER_CONTAINER: Final = "docker_container"
TYPE_SMTP: Final = "smtp"
TYPE_MYSQL: Final = "mariaDB_mySQL"
TYPE_POSTGRESQL: Final = "postgresql"
TYPE_REDIS: Final = "redis"
TYPE_HEARTBEAT: Final = "heartbeat"

# Types whose `config` carries a host and a port we can render as a target.
TYPES_HOST_PORT: Final = frozenset(
    {TYPE_TCP, TYPE_DNS, TYPE_SMTP, TYPE_REDIS, TYPE_MYSQL, TYPE_POSTGRESQL}
)
# Types whose `config` carries a bare URL.
TYPES_URL: Final = frozenset({TYPE_HTTP, TYPE_HTTPS})

# --- Uptime periods --------------------------------------------------------

UPTIME_PERIODS: Final[list[str]] = ["24h", "7d", "30d", "90d", "365d"]

# --- Repair issues ---------------------------------------------------------

ISSUE_NO_TEAM: Final = "no_team"

# UptimeSphere for Home Assistant

Brings a self-hosted [UptimeSphere](https://github.com/Code-Sphere-Development/UptimeSphere)
monitoring instance into Home Assistant: one device per monitor, the instance
dashboard as sensors, and a notification whenever a monitor changes state.

This integration is **read-only**. It never pauses, resumes or triggers checks —
it only reads.

## Installation

### HACS (custom repository)

1. HACS → ⋮ → *Custom repositories*
2. Repository: `https://github.com/Code-Sphere-Development/UptimeSphere-HomeAssistant`, category *Integration*
3. Install **UptimeSphere**, then restart Home Assistant.

### Manual

Copy `custom_components/uptimesphere` into your Home Assistant `config/custom_components/`
directory and restart.

## Setup

1. In UptimeSphere, open **API tokens** (`/tokens`) and create a token.
2. In Home Assistant: *Settings → Devices & services → Add integration → UptimeSphere*.
3. Enter the address of your instance (the site root, e.g. `https://uptime.example.com`)
   and paste the token.

Turn off *Verify SSL certificate* only if your instance uses a self-signed certificate.

## What you get

### Per monitor

| Entity | Notes |
|---|---|
| `binary_sensor.<monitor>_reachable` | `connectivity`; **unknown** while paused, in maintenance or pending — never a fake "off" |
| `sensor.<monitor>_status` | the seven UptimeSphere states, with the monitor's tags, location and target as attributes |
| `sensor.<monitor>_last_checked` | timestamp |
| `sensor.<monitor>_response_time` | ms, from the most recent check |
| `sensor.<monitor>_uptime_24_h` | percent |
| `sensor.<monitor>_uptime_7_days` … `_365_days` | percent, **disabled by default** |
| `sensor.<monitor>_ssl_certificate_expires_in` | days; only created for `https` monitors |
| `sensor.<monitor>_next_check` | disabled by default |

### Per instance

`sensor.…_monitors_total`, `_monitors_online`, `_monitors_offline`, `_monitors_warning`,
`_ssl_expiring_soon`, `_average_response_time`, plus `binary_sensor.…_monitor_down`.

## Notifications

Every status change fires the event `uptimesphere_monitor_status_changed`:

```json
{
  "entry_id": "01J…",
  "monitor_id": 1,
  "name": "Website",
  "type": "https",
  "old_status": "up",
  "new_status": "down",
  "last_checked_at": "2026-09-14T10:00:00.000000Z"
}
```

Transitions into or out of `pending` and `unknown` are suppressed — the backend passes
through those on every retry cycle, so reporting them would turn one outage into four
notifications.

Optionally, pick a notification service under *Configure*. It is called only for the
status transitions you select, and only for the monitors you select (empty = all).
The event fires regardless of those settings.

```yaml
automation:
  - alias: "UptimeSphere outage"
    triggers:
      - trigger: event
        event_type: uptimesphere_monitor_status_changed
        event_data:
          new_status: down
    actions:
      - action: notify.mobile_app_phone
        data:
          title: "{{ trigger.event.data.name }} is down"
          message: "was {{ trigger.event.data.old_status }}"
```

## Options

| Option | Default | Range |
|---|---|---|
| Polling interval | 60 s | 30–3600 s |
| Detail data interval | 30 min | 5–360 min |
| Notification service | off | any `notify.*` service |
| Notify on | down, up | down / up / warning / paused / maintenance |
| Only these monitors | all | any subset |

The **detail interval** governs response time, uptime percentages and SSL expiry.
Keep it generous: see *Load* below.

## Load

Two requests per polling cycle cover every monitor and the dashboard. Detail data costs
two further requests **per monitor**, which is why it runs on its own slow interval and
only for monitors that actually have an enabled entity — disabling a monitor's response
time, uptime and SSL entities removes that cost entirely.

Two things make requests more expensive than they look:

- `/monitors/{id}/uptime` runs ten `COUNT` queries server-side, one pair per time window.
- Every `/api/v1` request makes UptimeSphere call CodeSphere Accounts synchronously to
  re-resolve the session, because bearer-token requests have no session cache.

With 20 monitors and the defaults that is 2 requests/minute plus 40 requests every
30 minutes. Above roughly 100 monitors, raise both intervals.

## Known limitations

- **The team is whichever one you last selected in the web UI.** The API has no way to
  switch teams (`POST /teams/current` is browser-only), so monitors belonging to other
  teams are invisible here. If that matters, give Home Assistant its own UptimeSphere
  account with a single team.
- **A silent 403 after 30 days.** Every API request depends on the CodeSphere tokens
  UptimeSphere stores for your user. If you do not sign in through a browser for 30 days,
  the refresh token expires and the API answers 403 — with a perfectly valid API token.
  The integration raises a repair issue explaining this; re-entering the token would not
  help, signing in once does.
- **Polling only.** UptimeSphere has no broadcasting or WebSocket channel, so a status
  change is noticed at the next poll — up to one polling interval late.
- **Heartbeat monitors show no "last heartbeat".** The field exists on the server but is
  not part of the monitor API resource.
- **Read-only.** No pause, resume, maintenance or forced checks.
- **The token is stored like every Home Assistant credential**, unencrypted in
  `.storage/core.config_entries`. Use a dedicated token and revoke it at `/tokens` if the
  Home Assistant host is ever compromised.
- **AccountSphere OAuth login is not available yet.** The authentication layer is built
  around a strategy interface so the device-authorization flow can be added later without
  disturbing existing entries.

## Requirements

Home Assistant 2025.2.0 or newer.

CI runs the test suite on Python 3.13 and 3.14. That is not redundancy: the test
harness pins the Home Assistant version it tests against and drops interpreters
as Home Assistant does, so 3.13 exercises the oldest Home Assistant still
reachable and 3.14 exercises the current one.

## Development

```bash
python3.14 -m venv .venv && source .venv/bin/activate
pip install -r requirements_test.txt
ruff check . && pytest
```

## License

MIT — see [LICENSE](LICENSE).

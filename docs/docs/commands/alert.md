---
sidebar_position: 12
title: alert
---

# tinkr alert

Manage threshold-based alert rules. An alert rule fires a notification when a metric crosses a defined threshold, regardless of whether a watch is running.

```
tinkr alert <subcommand> [options]
```

## Subcommands

| Subcommand | Description |
|---|---|
| `create <service> <metric> <operator> <threshold>` | Create an alert rule |
| `list` | List all alert rules |
| `delete <alert-id>` | Delete an alert rule |
| `mute <alert-id>` | Mute an alert rule for a duration |

---

## `tinkr alert create`

```bash
tinkr alert create <service> <metric> <operator> <threshold> [options]
```

### Arguments

| Argument | Description |
|---|---|
| `service` | Service name |
| `metric` | Metric name (e.g. `error_count`, `latency_p99`) |
| `operator` | Comparison operator: `gt`, `lt`, `gte`, `lte` |
| `threshold` | Numeric threshold value |

### Options

| Flag | Default | Description |
|---|---|---|
| `--severity TEXT` | `medium` | `low`, `medium`, `high`, `critical` |
| `--notifier TEXT` | `default` | Notifier from your profile config |
| `--destination TEXT` | — | Override the notifier channel/URL |

### Examples

```bash
# Fire when error count exceeds 50
tinkr alert create payments-api error_count gt 50

# High severity p99 latency alert → PagerDuty
tinkr alert create payments-api latency_p99 gt 1000 --severity high --notifier pagerduty

# Alert when request rate drops below minimum
tinkr alert create payments-api request_rate lt 100 --severity critical

# Route to specific Slack channel
tinkr alert create auth-service error_count gt 20 --notifier slack-ops --destination "#auth-oncall"
```

### Output

```
Alert rule created
  ID:        alert-a3f2b1c4
  Service:   payments-api
  Condition: error_count > 50
  Severity:  medium
  Notifier:  default
```

---

## `tinkr alert list`

```bash
tinkr alert list
```

### Output

```
ALERT ID          SERVICE         METRIC          CONDITION   SEVERITY  NOTIFIER   STATUS
alert-a3f2b1c4   payments-api    error_count     > 50        medium    default    active
alert-b5c6d7e8   payments-api    latency_p99     > 1000      high      pagerduty  active
alert-9f8e7d6c   auth-service    error_count     > 20        medium    slack-ops  muted (until 16:00)
```

---

## `tinkr alert delete`

Permanently removes an alert rule.

```bash
tinkr alert delete alert-a3f2b1c4
```

---

## `tinkr alert mute`

Silence an alert rule for a period without deleting it. Useful during planned maintenance.

```bash
tinkr alert mute alert-a3f2b1c4 [options]
```

### Options

| Flag | Default | Description |
|---|---|---|
| `--for TEXT` | `30m` | Mute duration: `30m`, `2h`, `1d` |

### Examples

```bash
# Mute for 30 minutes (default)
tinkr alert mute alert-a3f2b1c4

# Mute for 2 hours during maintenance window
tinkr alert mute alert-a3f2b1c4 --for 2h

# Mute for a full day
tinkr alert mute alert-a3f2b1c4 --for 1d
```

---

## Alert rules vs watches

| | `tinkr alert` | `tinkr watch` |
|---|---|---|
| Trigger | Specific metric threshold | Any anomaly change |
| Granularity | Per metric, per threshold | Per service, all metrics |
| Use case | "Notify me if error_count > 50" | "Notify me of anything unusual" |
| Muting | Per rule | Per watch (stop) |

Use both together: watches catch unexpected anomalies, alert rules enforce hard SLO thresholds.

## See also

- [`tinkr watch`](watch) — continuous background monitoring
- [`tinkr slo`](slo) — compute error budget and burn rate
- [Slack Integration](../integrations/slack) — Slack notifier configuration
- [Webhooks](../integrations/webhooks) — webhook notifier configuration

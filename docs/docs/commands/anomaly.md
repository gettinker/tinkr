---
sidebar_position: 5
title: anomaly
---

# tinkr anomaly

Detect anomalies across all tracked metrics for a service. Compares current values against baselines and thresholds to surface statistically significant deviations.

```
tinkr anomaly <service> [options]
```

## Arguments

| Argument | Description |
|---|---|
| `service` | Service name as configured in the active profile |

## Options

| Flag | Default | Description |
|---|---|---|
| `--since TEXT` | `1h` | Look-back window — e.g. `30m`, `2h`, `6h` |
| `--severity TEXT` | — | Filter results: `low`, `medium`, `high`, `critical` |
| `--json` | off | Emit raw JSON |

## Examples

```bash
# Check for anomalies in the last hour
tinkr anomaly payments-api

# Last 30 minutes, high and above only
tinkr anomaly payments-api --since 30m --severity high

# All severity levels
tinkr anomaly payments-api --since 2h

# JSON for alerting pipelines
tinkr anomaly payments-api --since 1h --json
```

## Output

```
Anomalies detected for payments-api (since 1h)

  HIGH     error_count       847 errors in 10m — threshold 10
  MEDIUM   latency_p99       3.2s — threshold 1s
  LOW      db_conn_pool      82% utilization (baseline: 45%)
```

## Severity levels

| Level | Meaning |
|---|---|
| `critical` | Service is effectively down; immediate action required |
| `high` | Strong signal of an active incident |
| `medium` | Degradation detected; may escalate |
| `low` | Minor deviation; worth monitoring |

## Detection method

The backend computes anomalies by comparing the current window against a historical baseline (typically 1–7 days of the same time-of-day). Thresholds can be tuned via alert rules — see [`tinkr alert`](alert).

## Integration with watches

`tinkr watch` runs anomaly detection on a schedule and fires notifications when the anomaly set changes. See [`tinkr watch`](watch).

## See also

- [`tinkr rca`](rca) — stream a full AI root-cause analysis
- [`tinkr investigate`](investigate) — interactive investigation starting from anomalies
- [`tinkr watch`](watch) — continuous background monitoring
- [`tinkr alert`](alert) — threshold-based alert rules

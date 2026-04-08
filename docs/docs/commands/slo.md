---
sidebar_position: 10
title: slo
---

# tinker slo

Compute SLO (Service Level Objective) availability, error budget, and burn rate for a service.

```
tinker slo <service> [options]
```

## Arguments

| Argument | Description |
|---|---|
| `service` | Service name as configured in the active profile |

## Options

| Flag | Default | Description |
|---|---|---|
| `--target FLOAT` | `99.9` | SLO target as a percentage (e.g. `99.9`, `99.5`, `99.99`) |
| `--window TEXT` | `30d` | Rolling window — e.g. `7d`, `30d`, `90d` |
| `--json` | off | Emit raw JSON |

## Examples

```bash
# Default: 99.9% target over 30 days
tinker slo payments-api

# Four nines
tinker slo payments-api --target 99.99 --window 30d

# Weekly SLO check
tinker slo payments-api --target 99.9 --window 7d

# JSON for dashboards
tinker slo payments-api --target 99.9 --window 30d --json
```

## Output

```
SLO Report — payments-api
Target: 99.9%   Window: 30d

  Availability:     99.71%
  Error budget:     43.2 min remaining  (of 43.8 min total)
  Budget consumed:  98.6%  🔴

  Burn rate (1h):   14.2×   CRITICAL — budget exhausted in ~3h at this rate
  Burn rate (6h):    2.1×   WARNING  — budget exhausted in ~20h
  Burn rate (24h):   0.8×   OK

  Errors in window: 847
  Total requests:   302,400
```

## Availability calculation

Tinkr queries the log backend for error-level events and total request counts:

```
availability = (total_requests - error_requests) / total_requests × 100
```

Error budget:

```
budget_minutes = window_minutes × (1 - target/100)
consumed_minutes = window_minutes × (1 - availability/100)
remaining_minutes = budget_minutes - consumed_minutes
```

Burn rate (hourly):

```
burn_rate = hourly_error_rate / hourly_budget_rate
```

A burn rate > 1.0 means the error budget will be exhausted before the window ends if the rate continues.

## Common SLO targets

| Target | Downtime allowed (30d) |
|---|---|
| 99.0% | 7h 12m |
| 99.5% | 3h 36m |
| 99.9% | 43m 12s |
| 99.95% | 21m 36s |
| 99.99% | 4m 19s |

## See also

- [`tinker anomaly`](anomaly) — detect anomalies that may be consuming error budget
- [`tinker alert`](alert) — create burn-rate alerts
- [`tinker diff`](diff) — compare error rate between time windows

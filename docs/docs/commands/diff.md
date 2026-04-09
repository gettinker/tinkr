---
sidebar_position: 7
title: diff
---

# tinkr diff

Compare logs and metrics between two time windows side-by-side. Useful for identifying what changed before and after a deployment or incident.

```
tinkr diff <service> [options]
```

## Arguments

| Argument | Description |
|---|---|
| `service` | Service name as configured in the active profile |

## Options

| Flag | Default | Description |
|---|---|---|
| `--baseline TEXT` | `2h` | Older reference window (e.g. `24h` = yesterday same duration) |
| `--compare TEXT` | `1h` | Recent window to compare against the baseline |
| `--json` | off | Emit raw JSON |

## Examples

```bash
# Compare last hour vs the hour before
tinkr diff payments-api

# Compare last hour vs 24h ago
tinkr diff payments-api --baseline 24h --compare 1h

# Compare last 30m vs last 2h
tinkr diff payments-api --baseline 2h --compare 30m

# JSON for scripting
tinkr diff payments-api --baseline 24h --compare 1h --json
```

## Output

```
Diff for payments-api
  Baseline: 3h–2h ago    Compare: 1h–now

METRIC              BASELINE      COMPARE       CHANGE
error_count         12 / 10m      847 / 10m     +7058% ▲
latency_p99         320ms         3.2s          +900%  ▲
request_rate        1240/min      1190/min       −4%   ▼
db_query_time_avg   45ms          52ms          +16%   ▲

TOP LOG PATTERNS
  + [847x] ERROR  Payment charge failed: card_declined
  + [203x] ERROR  Stripe API timeout
  - [ 12x] ERROR  Validation failed (was present in baseline)
  ~ [1240x] INFO  Request processed (rate unchanged)
```

- `+` means the pattern appeared or increased significantly in the compare window
- `-` means the pattern decreased or disappeared
- `~` means roughly unchanged

## Use cases

**Post-deployment validation**: compare traffic before and after a release.

```bash
tinkr diff payments-api --baseline 30m --compare 30m
```

Run this immediately after `git push` to catch regressions.

**Incident scope**: see exactly what changed when an alert fired.

```bash
tinkr diff payments-api --baseline 24h --compare 1h
```

**Regular Tuesday spike**: confirm a known pattern rather than investigating it.

## See also

- [`tinkr anomaly`](anomaly) — automated anomaly detection
- [`tinkr deploy`](deploy) — correlate diffs with specific commits
- [`tinkr rca`](rca) — full AI root-cause analysis

---
sidebar_position: 1
title: Commands Overview
---

# CLI Command Reference

All commands share a common structure:

```
tinker [global-options] <command> [command-options]
```

## Global options

| Flag | Description |
|---|---|
| `--profile TEXT` | Use a named profile from `~/.tinkr/config.toml` (default: `default`) |
| `--server TEXT` | Override the Tinkr server URL |
| `--token TEXT` | Override the API token |
| `--json` | Emit raw JSON instead of formatted output |
| `--help` | Show help and exit |

---

## Command summary

| Command | Purpose |
|---|---|
| [`tinker logs`](./logs.md) | Fetch recent log lines from a service |
| [`tinker tail`](./tail.md) | Stream live logs to the terminal |
| [`tinker metrics`](./metrics.md) | Query metric time series |
| [`tinker anomaly`](./anomaly.md) | Detect anomalies across all metrics |
| [`tinker trace`](./trace.md) | Retrieve distributed traces |
| [`tinker diff`](./diff.md) | Compare two time windows side-by-side |
| [`tinker investigate`](./investigate.md) | Interactive RCA REPL |
| [`tinker rca`](./rca.md) | Streaming AI root-cause analysis |
| [`tinker slo`](./slo.md) | Compute SLO availability and error budget |
| [`tinker watch`](./watch.md) | Manage continuous background monitoring |
| [`tinker alert`](./alert.md) | Manage threshold-based alert rules |
| [`tinker deploy`](./deploy.md) | List deploys and correlate with anomalies |
| [`tinker profile`](./profile.md) | Manage named profiles |

---

## Quick-reference examples

```bash
# Pull last 30 minutes of errors
tinker logs payments-api --since 30m --filter level:ERROR

# Stream live
tinker tail payments-api --filter level:ERROR

# Query a metric
tinker metrics payments-api --metric http_requests_total --since 1h

# Detect anomalies
tinker anomaly payments-api --since 1h

# Trace last 20 requests
tinker trace payments-api --since 30m --limit 20

# Compare this hour vs last hour
tinker diff payments-api --baseline 2h --compare 1h

# Start interactive investigation
tinker investigate payments-api

# Streaming AI root-cause analysis
tinker rca payments-api --since 2h

# Check SLO
tinker slo payments-api --target 99.9 --window 30d

# Continuous monitoring
tinker watch start payments-api
tinker watch list
tinker watch stop watch-abc123
tinker watch delete watch-abc123

# Alert rules
tinker alert create payments-api error_count gt 50 --severity high
tinker alert list
tinker alert mute alert-abc123 --for 2h
tinker alert delete alert-abc123

# Deploy correlation
tinker deploy list payments-api --since 7d
tinker deploy correlate payments-api --since 7d

# Profile management
tinker profile list
tinker profile use aws-prod
tinker profile show
```

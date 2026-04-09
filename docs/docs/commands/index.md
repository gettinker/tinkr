---
sidebar_position: 1
title: Commands Overview
---

# CLI Command Reference

All commands share a common structure:

```
tinkr [global-options] <command> [command-options]
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
| [`tinkr logs`](./logs.md) | Fetch recent log lines from a service |
| [`tinkr tail`](./tail.md) | Stream live logs to the terminal |
| [`tinkr metrics`](./metrics.md) | Query metric time series |
| [`tinkr anomaly`](./anomaly.md) | Detect anomalies across all metrics |
| [`tinkr trace`](./trace.md) | Retrieve distributed traces |
| [`tinkr diff`](./diff.md) | Compare two time windows side-by-side |
| [`tinkr investigate`](./investigate.md) | Interactive RCA REPL |
| [`tinkr rca`](./rca.md) | Streaming AI root-cause analysis |
| [`tinkr slo`](./slo.md) | Compute SLO availability and error budget |
| [`tinkr watch`](./watch.md) | Manage continuous background monitoring |
| [`tinkr alert`](./alert.md) | Manage threshold-based alert rules |
| [`tinkr deploy`](./deploy.md) | List deploys and correlate with anomalies |
| [`tinkr profile`](./profile.md) | Manage named profiles |

---

## Quick-reference examples

```bash
# Pull last 30 minutes of errors
tinkr logs payments-api --since 30m --filter level:ERROR

# Stream live
tinkr tail payments-api --filter level:ERROR

# Query a metric
tinkr metrics payments-api --metric http_requests_total --since 1h

# Detect anomalies
tinkr anomaly payments-api --since 1h

# Trace last 20 requests
tinkr trace payments-api --since 30m --limit 20

# Compare this hour vs last hour
tinkr diff payments-api --baseline 2h --compare 1h

# Start interactive investigation
tinkr investigate payments-api

# Streaming AI root-cause analysis
tinkr rca payments-api --since 2h

# Check SLO
tinkr slo payments-api --target 99.9 --window 30d

# Continuous monitoring
tinkr watch start payments-api
tinkr watch list
tinkr watch stop watch-abc123
tinkr watch delete watch-abc123

# Alert rules
tinkr alert create payments-api error_count gt 50 --severity high
tinkr alert list
tinkr alert mute alert-abc123 --for 2h
tinkr alert delete alert-abc123

# Deploy correlation
tinkr deploy list payments-api --since 7d
tinkr deploy correlate payments-api --since 7d

# Profile management
tinkr profile list
tinkr profile use aws-prod
tinkr profile show
```

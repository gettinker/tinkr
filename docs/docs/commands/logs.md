---
sidebar_position: 2
title: logs
---

# tinkr logs

Fetch recent log lines from a service.

```
tinkr logs <service> [options]
```

## Arguments

| Argument | Description |
|---|---|
| `service` | Service name as configured in the active profile |

## Options

| Flag | Default | Description |
|---|---|---|
| `--since TEXT` | `1h` | How far back to look — e.g. `30m`, `2h`, `24h` |
| `--filter TEXT` | — | LogQL / filter expression (e.g. `level:ERROR`, `"NullPointerException"`) |
| `--limit INT` | `100` | Maximum number of log lines to return |
| `--json` | off | Emit raw JSON |

## Examples

```bash
# Last hour of logs
tinkr logs payments-api

# Last 30 minutes, errors only
tinkr logs payments-api --since 30m --filter level:ERROR

# Search for a specific exception
tinkr logs payments-api --since 2h --filter "NullPointerException"

# Return up to 500 lines
tinkr logs payments-api --since 6h --limit 500

# Machine-readable output
tinkr logs payments-api --since 1h --json
```

## Output

```
[14:01:03] ERROR  payments-api  Payment charge failed: card_declined (card_id=card_abc123)
[14:01:04] ERROR  payments-api  Stripe API timeout after 30s (attempt 3/3)
[14:01:05] WARN   payments-api  Retry queue depth 847 — exceeds soft limit of 100
```

## Backend log query language

| Backend | Query language |
|---|---|
| Grafana (Loki) | LogQL — `{app="payments-api"} \|= "ERROR"` |
| CloudWatch | CloudWatch Logs Insights — `filter @message like /ERROR/` |
| GCP | Cloud Logging filter — `severity=ERROR` |
| Azure | KQL — `AppTraces \| where SeverityLevel >= 3` |
| Datadog | Log query — `service:payments-api status:error` |
| Elastic | Elasticsearch DSL — `{"match": {"level": "ERROR"}}` |
| OTel | OpenSearch DSL |

The `--filter` value is passed to the backend's native query engine. Use the syntax appropriate for your backend.

## See also

- [`tinkr tail`](tail) — stream logs live
- [`tinkr investigate`](investigate) — start an AI-powered investigation from log context

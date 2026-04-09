---
sidebar_position: 6
title: trace
---

# tinkr trace

Retrieve distributed traces for a service and display the slowest or most error-prone requests.

```
tinkr trace <service> [options]
```

## Arguments

| Argument | Description |
|---|---|
| `service` | Service name as configured in the active profile |

## Options

| Flag | Default | Description |
|---|---|---|
| `--since TEXT` | `1h` | Look-back window — e.g. `30m`, `2h`, `24h` |
| `--limit INT` | `20` | Maximum number of traces to return |
| `--tags TEXT` | — | Filter by tag key=value pairs (e.g. `http.status_code=500`) |
| `--json` | off | Emit raw JSON |

## Examples

```bash
# Last 20 traces
tinkr trace payments-api

# Last 30 minutes, up to 50 traces
tinkr trace payments-api --since 30m --limit 50

# Only failed traces
tinkr trace payments-api --tags http.status_code=500

# JSON output
tinkr trace payments-api --since 1h --json
```

## Output

```
Traces for payments-api (last 1h, 20 results)

  TRACE ID              OPERATION               DURATION   SPANS  STATUS
  7f3a2b1c4d5e6f78      POST /v1/charges        4.2s       12     ERROR
  1a2b3c4d5e6f7a8b      POST /v1/charges        1.1s        8     OK
  9e8d7c6b5a4f3e2d      GET  /v1/customers       98ms        3     OK
  ...

  Slowest trace: 7f3a2b1c4d5e6f78

  ┌ POST /v1/charges (payments-api)  4.2s
  ├── validate_card                   12ms
  ├── db.query customers              45ms
  ├── stripe.charge (external)        3.8s  ← slow
  └── db.insert payment_events        22ms
```

## Tracing backends

| Backend | Tracing system |
|---|---|
| Grafana | Grafana Tempo |
| CloudWatch | AWS X-Ray |
| GCP | Google Cloud Trace |
| Azure | Application Insights |
| Datadog | Datadog APM |
| Elastic | Elastic APM |
| OTel | OpenSearch (OTel Collector) |

## Tracing requirements

Tracing must be enabled in your application. Tinkr reads from the tracing backend — it does not instrument your code.

- **Grafana**: Configure your app to send spans to Tempo
- **CloudWatch**: Enable X-Ray tracing in your ECS task definition / Lambda configuration
- **GCP**: Enable Cloud Trace API; use the OpenTelemetry or Cloud Trace SDK
- **Azure**: Enable Application Insights; SDK auto-instruments most frameworks
- **Datadog**: Install the Datadog APM library; traces flow through the Datadog agent
- **Elastic**: Use the Elastic APM agent; traces go to the APM server
- **OTel**: Send spans to the OTel Collector → OpenSearch backend

## See also

- [`tinkr investigate`](investigate) — traces are included in the investigation context
- [`tinkr rca`](rca) — RCA fetches traces automatically

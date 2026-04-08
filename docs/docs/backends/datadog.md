---
sidebar_position: 6
title: Datadog
---

# Datadog Backend

Uses the Datadog Logs API v2 for logs, Metrics API v1 for time series, and APM Traces API v2 for distributed tracing.

```bash
TINKR_BACKEND=datadog
```

---

## Authentication

Datadog requires two keys:

- **API key** (`DD_API_KEY`) — identifies your organization; used for all API calls
- **Application key** (`DD_APP_KEY`) — grants read access to your Datadog account data

Both must be present for Tinker to function.

Store them in your cloud secrets manager (AWS Secrets Manager, GCP Secret Manager, or Azure Key Vault) and inject as environment variables. Never hardcode them.

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `DD_API_KEY` | Yes | Datadog API key |
| `DD_APP_KEY` | Yes | Datadog application key |
| `DD_SITE` | No | Datadog site (default: `datadoghq.com`; EU: `datadoghq.eu`) |

---

## Profile configuration

```toml title="~/.tinkr/config.toml"
[profiles.datadog-prod]
backend = "datadog"
site    = "datadoghq.com"
```

```bash title="~/.tinkr/.env"
DD_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
DD_APP_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

---

## Log query

Tinker calls the Datadog Logs API v2 (`POST /api/v2/logs/events/search`):

```json
{
  "filter": {
    "query": "service:payments-api status:error",
    "from": "now-1h",
    "to": "now"
  },
  "sort": "-timestamp",
  "page": { "limit": 100 }
}
```

The service name is matched using the `service` tag. Ensure your application sets the `DD_SERVICE` environment variable or `service` tag.

---

## Metrics

Tinker calls the Datadog Metrics API v1 (`GET /api/v1/query`):

```
q=avg:trace.web.request{service:payments-api}.as_count()&from=...&to=...
```

Common Datadog metrics:

| Metric | Description |
|---|---|
| `trace.web.request` | HTTP request count |
| `trace.web.request.duration` | Request duration |
| `trace.web.request.errors` | Request error count |
| `aws.ecs.cpuutilization` | ECS container CPU |
| `aws.ecs.memory_utilization` | ECS container memory |
| `system.cpu.user` | Host CPU |
| `system.mem.used` | Host memory |

---

## Distributed tracing (APM)

Tinker calls the Datadog APM Traces API v2 (`POST /api/v2/spans/events/search`):

```json
{
  "filter": {
    "query": "@service:payments-api",
    "from": "now-1h",
    "to": "now"
  }
}
```

APM must be enabled in your application. Use the Datadog APM library (`ddtrace`) or the OpenTelemetry SDK with the Datadog exporter.

```bash
# Python
pip install ddtrace
ddtrace-run python app.py

# Node.js
DD_TRACE_ENABLED=true node app.js
```

Traces are automatically correlated with logs when `DD_LOGS_INJECTION=true` is set.

---

## Datadog site

| Region | `DD_SITE` |
|---|---|
| US1 (default) | `datadoghq.com` |
| EU | `datadoghq.eu` |
| US3 | `us3.datadoghq.com` |
| US5 | `us5.datadoghq.com` |
| AP1 | `ap1.datadoghq.com` |
| Gov | `ddog-gov.com` |

---

## Local development

```bash
export TINKR_BACKEND=datadog
export DD_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
export DD_APP_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
uv run tinkr-server
```

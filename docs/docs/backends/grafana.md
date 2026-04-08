---
sidebar_position: 5
title: Grafana Stack
---

# Grafana Stack Backend

Uses Loki (LogQL) for logs, Prometheus (PromQL) for metrics, and Grafana Tempo for distributed tracing. The best choice for self-hosted environments and local development.

```bash
TINKR_BACKEND=grafana
```

---

## Components

| Component | Purpose | Default port |
|---|---|---|
| Loki | Log aggregation | 3100 |
| Prometheus | Metrics storage | 9090 |
| Tempo | Distributed tracing | 3200 |
| Grafana UI | Visualization | 3000 |

All four are included in the Tinkr Docker Compose stack.

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `GRAFANA_LOKI_URL` | Yes | Loki base URL (e.g. `http://loki:3100`) |
| `GRAFANA_PROMETHEUS_URL` | Yes | Prometheus base URL (e.g. `http://prometheus:9090`) |
| `GRAFANA_TEMPO_URL` | No | Tempo base URL (e.g. `http://tempo:3200`) |
| `GRAFANA_API_KEY` | No | Grafana API key (for managed Grafana Cloud) |

---

## Profile configuration

```toml title="~/.tinkr/config.toml"
[profiles.default]
backend        = "grafana"
loki_url       = "env:GRAFANA_LOKI_URL"
prometheus_url = "env:GRAFANA_PROMETHEUS_URL"
tempo_url      = "env:GRAFANA_TEMPO_URL"
```

---

## Log query (LogQL)

Tinkr constructs LogQL queries against Loki:

```logql
{app="payments-api"} |= "ERROR" | json | line_format "{{.level}} {{.message}}"
```

The `app` label must match your Loki label configuration. Tinkr uses the service name as the label value.

### Label configuration

Ensure your log shipper (Promtail, Alloy, Fluent Bit) sets an `app` or `service_name` label:

```yaml title="promtail-config.yml"
scrape_configs:
  - job_name: payments-api
    static_configs:
      - targets: [localhost]
        labels:
          app: payments-api
          __path__: /var/log/payments-api/*.log
```

---

## Metrics (PromQL)

Tinkr queries Prometheus using PromQL:

```promql
rate(http_requests_total{job="payments-api"}[5m])
```

The service name is matched against the `job` label. Ensure your scrape config sets the `job` label to match the Tinkr service name.

### Common Prometheus metrics

```promql
# Request rate
rate(http_requests_total[5m])

# Error rate
rate(http_requests_total{status=~"5.."}[5m])

# P99 latency
histogram_quantile(0.99, rate(http_request_duration_seconds_bucket[5m]))

# Memory usage
process_resident_memory_bytes
```

---

## Distributed tracing (Tempo)

Tinkr queries Tempo's HTTP API to search traces:

```http
GET /api/search?service.name=payments-api&start=1712498400&end=1712502000
```

Traces must be sent to Tempo. Most OpenTelemetry SDKs can export to Tempo via OTLP:

```yaml title="otel-collector-config.yml"
exporters:
  otlp:
    endpoint: http://tempo:4317
```

Or configure the OTLP SDK directly:

```python
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
exporter = OTLPSpanExporter(endpoint="http://tempo:4317", insecure=True)
```

---

## Local development

Run Tinkr locally with Loki + Prometheus + Grafana using Docker:

```bash
# Pull and run Tinkr
docker run -d --name tinker -p 8000:8000 \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e TINKR_BACKEND=grafana \
  -e GRAFANA_LOKI_URL=http://loki:3100 \
  -e GRAFANA_PROMETHEUS_URL=http://prometheus:9090 \
  -v ~/.tinkr:/root/.tinkr \
  tinker:local
```

Build the image first:

```bash
git clone https://github.com/gettinker/tinkr && cd tinkr && docker build -t tinker:local .
```

Or run directly from source:

```bash
git clone https://github.com/gettinker/tinkr
cd tinkr
cd tinker
uv sync
TINKR_BACKEND=grafana uv run tinker-server
```

---

## Grafana Cloud

For Grafana Cloud (managed), set:

```bash
GRAFANA_LOKI_URL=https://logs-prod-xxx.grafana.net
GRAFANA_PROMETHEUS_URL=https://prometheus-prod-xxx.grafana.net
GRAFANA_API_KEY=glsa_xxxxxxxxxxxx
```

Authentication is via the `Authorization: Bearer <api-key>` header on all Loki and Prometheus requests.

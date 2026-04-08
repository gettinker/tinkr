---
sidebar_position: 8
title: OpenTelemetry
---

# OpenTelemetry Backend

Uses OpenSearch for log storage, Prometheus for metrics, and OpenSearch for OTel trace storage. Best for organizations standardizing on the OpenTelemetry Collector.

```bash
TINKR_BACKEND=otel
```

---

## Architecture

```
Application
    │  (OTel SDK)
    ▼
OTel Collector
    ├──► OpenSearch (logs + traces)
    └──► Prometheus (metrics)
         │
         ▼
      Tinkr (TINKR_BACKEND=otel)
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `OTEL_OPENSEARCH_URL` | Yes | OpenSearch base URL (e.g. `http://opensearch:9200`) |
| `OTEL_OPENSEARCH_API_KEY` | No | OpenSearch API key (if auth enabled) |
| `OTEL_PROMETHEUS_URL` | Yes | Prometheus base URL (e.g. `http://prometheus:9090`) |
| `OTEL_LOG_INDEX` | No | Log index prefix (default: `otel-logs-*`) |
| `OTEL_TRACE_INDEX` | No | Trace index prefix (default: `otel-traces-*`) |

---

## Profile configuration

```toml title="~/.tinkr/config.toml"
[profiles.otel-prod]
backend        = "otel"
opensearch_url = "env:OTEL_OPENSEARCH_URL"
prometheus_url = "env:OTEL_PROMETHEUS_URL"
log_index      = "otel-logs-*"
trace_index    = "otel-traces-*"
```

---

## OTel Collector configuration

Configure the collector to export to OpenSearch and Prometheus:

```yaml title="otel-collector-config.yml"
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
  resource:
    attributes:
      - key: service.name
        action: upsert

exporters:
  opensearch:
    http:
      endpoint: http://opensearch:9200
    logs_index: otel-logs
    traces_index: otel-traces

  prometheusremotewrite:
    endpoint: http://prometheus:9090/api/v1/write

service:
  pipelines:
    logs:
      receivers: [otlp]
      processors: [batch, resource]
      exporters: [opensearch]
    traces:
      receivers: [otlp]
      processors: [batch, resource]
      exporters: [opensearch]
    metrics:
      receivers: [otlp]
      processors: [batch]
      exporters: [prometheusremotewrite]
```

---

## Log query (OpenSearch DSL)

Tinkr queries OpenSearch using the same DSL as Elasticsearch:

```json
{
  "query": {
    "bool": {
      "must": [
        { "term": { "resource.service.name": "payments-api" } },
        { "term": { "severity": "ERROR" } },
        { "range": { "@timestamp": { "gte": "now-1h" } } }
      ]
    }
  },
  "sort": [{ "@timestamp": { "order": "desc" } }],
  "size": 100
}
```

OTel log field mapping:

| Tinkr field | OTel field |
|---|---|
| `service` | `resource.service.name` |
| `level` | `severity` |
| `timestamp` | `@timestamp` |
| `message` | `body` |

---

## Metrics (Prometheus)

Tinkr queries Prometheus using the same PromQL interface as the Grafana backend. See [Grafana backend](grafana#metrics-promql) for common metric names.

For OTel SDK metrics, names follow the semantic conventions:

```promql
# HTTP request rate (OTel naming)
rate(http_server_requests_total{service_name="payments-api"}[5m])

# RPC duration
rate(rpc_server_duration_bucket[5m])
```

---

## Distributed tracing (OpenSearch)

Tinkr queries the `otel-traces-*` index for root spans (spans where `parentSpanId` is empty):

```json
{
  "query": {
    "bool": {
      "must": [
        { "term": { "resource.service.name": "payments-api" } },
        { "range": { "startTime": { "gte": "now-1h" } } },
        { "term": { "parentSpanId": "" } }
      ]
    }
  },
  "sort": [{ "duration": { "order": "desc" } }],
  "size": 20
}
```

---

## Application instrumentation

Use the OpenTelemetry SDK and point it at the collector:

```python title="Python"
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider()
provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint="http://otel-collector:4317"))
)
trace.set_tracer_provider(provider)
```

```javascript title="Node.js"
const { NodeSDK } = require('@opentelemetry/sdk-node');
const { OTLPTraceExporter } = require('@opentelemetry/exporter-trace-otlp-grpc');

const sdk = new NodeSDK({
  traceExporter: new OTLPTraceExporter({ url: 'http://otel-collector:4317' }),
});
sdk.start();
```

---

## OpenSearch vs Elasticsearch

The `otel` backend uses the OpenSearch client, which is compatible with OpenSearch 1.x / 2.x. If you're using Elasticsearch as your OTel storage backend, use the `elastic` backend instead.

---

## Local development

```bash
docker run -p 9200:9200 -e "discovery.type=single-node" \
  opensearchproject/opensearch:latest

export TINKR_BACKEND=otel
export OTEL_OPENSEARCH_URL=http://localhost:9200
export OTEL_PROMETHEUS_URL=http://localhost:9090
uv run tinkr-server
```

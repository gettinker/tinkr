---
sidebar_position: 7
title: Elastic
---

# Elastic Backend

Uses Elasticsearch DSL for log queries, aggregations for metrics, and Elastic APM for distributed tracing.

```bash
TINKR_BACKEND=elastic
```

---

## Authentication

Tinker authenticates to Elasticsearch using an API key. Create one in Kibana:

**Stack Management → Security → API Keys → Create API Key**

Grant the key:
- `read` on indices `logs-*`, `filebeat-*`, `traces-*`, `apm-*`
- `monitor` on cluster

Or use a username/password for simpler setups.

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `ELASTIC_URL` | Yes | Elasticsearch base URL (e.g. `https://elastic.acme.internal:9200`) |
| `ELASTIC_API_KEY` | Yes (or user/pass) | Base64-encoded API key from Kibana |
| `ELASTIC_USERNAME` | Alt to API key | Elasticsearch username |
| `ELASTIC_PASSWORD` | Alt to API key | Elasticsearch password |
| `ELASTIC_INDEX_PATTERN` | No | Log index pattern (default: `logs-*,filebeat-*`) |

---

## Profile configuration

```toml title="~/.tinkr/config.toml"
[profiles.elastic-prod]
backend       = "elastic"
url           = "env:ELASTIC_URL"
index_pattern = "logs-*,filebeat-*"
```

```bash title="~/.tinkr/.env"
ELASTIC_URL=https://elastic.acme.internal:9200
ELASTIC_API_KEY=VnVhQ2ZHY0JDZGJrZXctATxxxxxxxxxxxxxxxx==
```

---

## Log query (Elasticsearch DSL)

Tinker queries using the Elasticsearch DSL:

```json
{
  "query": {
    "bool": {
      "must": [
        { "match": { "service.name": "payments-api" } },
        { "match": { "log.level": "ERROR" } },
        { "range": { "@timestamp": { "gte": "now-1h", "lte": "now" } } }
      ]
    }
  },
  "sort": [{ "@timestamp": { "order": "desc" } }],
  "size": 100
}
```

Field mapping (ECS-aligned):

| Tinker field | Elasticsearch field |
|---|---|
| `service` | `service.name` |
| `level` | `log.level` |
| `timestamp` | `@timestamp` |
| `message` | `message` |

If your indices use a different field layout, set `ELASTIC_INDEX_PATTERN` to target the right indices.

---

## Metrics (aggregations)

Tinker uses Elasticsearch date-histogram aggregations to compute metric time series:

```json
{
  "query": { "term": { "service.name": "payments-api" } },
  "aggs": {
    "over_time": {
      "date_histogram": { "field": "@timestamp", "fixed_interval": "5m" },
      "aggs": { "error_count": { "filter": { "term": { "log.level": "ERROR" } } } }
    }
  }
}
```

---

## Distributed tracing (Elastic APM)

Tinker queries the `traces-*` and `apm-*` indices for root spans (spans with no `parent.id`):

```json
{
  "query": {
    "bool": {
      "must": [
        { "term": { "service.name": "payments-api" } },
        { "range": { "@timestamp": { "gte": "now-1h" } } }
      ],
      "must_not": [{ "exists": { "field": "parent.id" } }]
    }
  },
  "sort": [{ "transaction.duration.us": { "order": "desc" } }],
  "size": 20
}
```

To send traces from your application, use the Elastic APM agent:

```python
# Python
import elasticapm
client = elasticapm.Client(service_name="payments-api", server_url="http://apm-server:8200")
```

Or use the OpenTelemetry SDK with the OTLP exporter pointing to the Elastic APM server (which accepts OTLP natively since 7.16).

---

## Elastic Cloud (hosted)

For Elastic Cloud, use the Cloud ID + API key:

```bash
ELASTIC_URL=https://xxxxxxxxxxxx.es.us-east-1.aws.elastic-cloud.com:9243
ELASTIC_API_KEY=VnVhQ2ZHY0JDZGJrZXctATxxxxxxxxxxxxxxxx==
```

---

## Local development

```bash
# Run Elasticsearch + Kibana locally
docker run -p 9200:9200 -e "discovery.type=single-node" elasticsearch:8.x

export TINKR_BACKEND=elastic
export ELASTIC_URL=http://localhost:9200
export ELASTIC_USERNAME=elastic
export ELASTIC_PASSWORD=changeme
uv run tinkr-server
```

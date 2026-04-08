---
sidebar_position: 1
title: Backends Overview
---

# Supported Backends

Tinkr works with every major cloud observability stack and popular self-hosted solutions. The active backend is selected by the `TINKR_BACKEND` environment variable at server startup.

## Backend comparison

| Backend | `TINKR_BACKEND` | Logs | Metrics | Traces | Auth |
|---|---|---|---|---|---|
| [AWS CloudWatch](./cloudwatch.md) | `cloudwatch` | CloudWatch Logs Insights | GetMetricData | X-Ray | IAM Role |
| [Google Cloud](./gcp.md) | `gcp` | Cloud Logging | Cloud Monitoring | Cloud Trace | Workload Identity |
| [Azure Monitor](./azure.md) | `azure` | Log Analytics (KQL) | Azure Monitor Metrics | App Insights | Managed Identity |
| [Grafana Stack](./grafana.md) | `grafana` | Loki (LogQL) | Prometheus (PromQL) | Tempo | API key |
| [Datadog](./datadog.md) | `datadog` | Logs API v2 | Metrics API v1 | APM | API key + App key |
| [Elastic](./elastic.md) | `elastic` | Elasticsearch DSL | Aggregations | Elastic APM | API key |
| [OpenTelemetry](./otel.md) | `otel` | OpenSearch | Prometheus | OTel Collector → OpenSearch | API key |

---

## Design principles

### One backend per server instance

`TINKR_BACKEND` is read once at startup. The backend is selected, credentials are resolved from the cloud identity mechanism (IAM role, Workload Identity, Managed Identity), and the server starts.

For multi-cloud environments, deploy one Tinkr instance per cloud account/project and use [profiles](../commands/profile.md) to route CLI requests to the right instance.

### Credential model

Cloud backends use the cloud provider's native identity — no long-lived API keys on the server:

```
AWS       → IAM Task Role (ECS/Fargate) or instance profile (EC2)
GCP       → Workload Identity (Cloud Run/GKE) or ADC (local)
Azure     → Managed Identity (Container Apps/AKS) or CLI (local)
Grafana   → API key or basic auth (set in config/env)
Datadog   → DD_API_KEY + DD_APP_KEY (set in secrets manager)
Elastic   → API key (set in secrets manager)
OTel      → Depends on deployment; typically OpenSearch API key
```

### ObservabilityBackend ABC

All backends implement the same abstract base class:

```python
class ObservabilityBackend(ABC):
    async def query_logs(self, service, since, filter_pattern, limit) -> list[LogEntry]
    async def get_metrics(self, service, metric_name, since) -> list[MetricPoint]
    async def detect_anomalies(self, service, since) -> list[Anomaly]
    async def get_traces(self, service, since, limit, tags) -> list[Trace]
```

The agent never imports a specific backend class — it always talks to the ABC.

---

## Choosing a backend

- **Already on AWS?** → Use `cloudwatch`. Zero additional infrastructure.
- **Already on GCP?** → Use `gcp`. Zero additional infrastructure.
- **Already on Azure?** → Use `azure`. Zero additional infrastructure.
- **Self-hosted or multi-cloud?** → Use `grafana` (Loki + Prometheus + Tempo).
- **Already paying for Datadog?** → Use `datadog`.
- **Already running Elastic?** → Use `elastic`.
- **Standardizing on OpenTelemetry?** → Use `otel`.

## Local development

For local development, use the Grafana backend — no cloud credentials needed:

```bash
git clone https://github.com/gettinker/tinkr && cd tinkr
docker build -t tinkr:local .
docker run -d --name tinkr -p 8000:8000 \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e TINKR_BACKEND=grafana \
  -e GRAFANA_LOKI_URL=http://loki:3100 \
  -e GRAFANA_PROMETHEUS_URL=http://prometheus:9090 \
  tinkr:local
```

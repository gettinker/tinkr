---
sidebar_position: 3
title: Google Cloud
---

# Google Cloud Backend

Uses Cloud Logging for log queries, Cloud Monitoring for metrics, and Cloud Trace for distributed tracing.

```bash
TINKER_BACKEND=gcp
```

---

## Authentication

The backend uses Application Default Credentials (`google-auth`):

1. Workload Identity (Cloud Run / GKE) — **recommended for production**
2. Service account key file (`GOOGLE_APPLICATION_CREDENTIALS`) — avoid in production
3. `gcloud auth application-default login` — local development

**No credentials go in the Tinker config.** Attach Workload Identity to your Cloud Run service or GKE service account.

---

## Required IAM roles

Assign to the service account:

| Role | Purpose |
|---|---|
| `roles/logging.viewer` | Read Cloud Logging entries |
| `roles/monitoring.viewer` | Read Cloud Monitoring metrics |
| `roles/cloudtrace.user` | Read Cloud Trace data |

```bash
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:tinker-sa@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/logging.viewer"

gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:tinker-sa@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/monitoring.viewer"

gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:tinker-sa@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/cloudtrace.user"
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `GCP_PROJECT_ID` | Yes | GCP project ID |

---

## Profile configuration

```toml title="~/.tinker/config.toml"
[profiles.gcp-prod]
backend    = "gcp"
project_id = "acme-prod-123456"

[profiles.gcp-prod.services.payments-api]
repo          = "acme/payments"
resource_type = "cloudrun"
```

---

## Log query

Tinker uses the Cloud Logging API with a structured filter:

```
resource.type = "cloud_run_revision"
resource.labels.service_name = "payments-api"
severity >= ERROR
timestamp >= "2026-04-07T13:00:00Z"
```

Log fields are mapped to Tinker's `LogEntry` schema using the `jsonPayload` or `textPayload` field.

---

## Metrics

Tinker calls the Cloud Monitoring `timeSeries.list` API. Common metric types:

| Service | Metric type |
|---|---|
| Cloud Run | `run.googleapis.com/request_count` |
| Cloud Run | `run.googleapis.com/request_latencies` |
| GKE | `kubernetes.io/container/cpu/request_utilization` |
| Cloud SQL | `cloudsql.googleapis.com/database/queries` |

---

## Distributed tracing (Cloud Trace)

Cloud Trace must be enabled in your application. Most Google Cloud services enable it automatically.

For custom applications, use the OpenTelemetry SDK with the Cloud Trace exporter:

```python
from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor
```

Tinker calls `cloudtrace.projects.traces.list` to fetch recent traces.

---

## Deployment

See [Deploying on GCP](../deployment/gcp) for Cloud Run, GKE, Workload Identity, and Secret Manager setup.

---

## Local development

```bash
gcloud auth application-default login

export TINKER_BACKEND=gcp
export GCP_PROJECT_ID=acme-dev-123456
uv run tinkr-server
```

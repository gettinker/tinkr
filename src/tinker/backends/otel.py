"""Universal observability backend: OpenSearch (logs) + Prometheus (metrics).

This is the recommended backend for multi-cloud deployments.

Data flow
---------
All cloud providers ship telemetry through an OpenTelemetry Collector:

  AWS CloudWatch Logs  ─┐
  GCP Cloud Logging    ─┼──► OTel Collector ──► OpenSearch  (logs + traces)
  Azure Monitor        ─┤                  ──► Prometheus   (metrics)
  Application logs     ─┘

Tinker then queries a single OpenSearch cluster and a single Prometheus
endpoint, regardless of where the workloads run.

CloudWatch / GCP metrics exporters
-----------------------------------
- CloudWatch → Prometheus:  github.com/prometheus-community/yet-another-cloudwatch-exporter
- GCP → Prometheus:          github.com/prometheus-community/stackdriver-exporter
- Azure → Prometheus:        github.com/RobustPerception/azure_metrics_exporter
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from tinker.backends.base import Anomaly, LogEntry, MetricPoint, ObservabilityBackend
from tinker.agent.guardrails import sanitize_log_content

log = structlog.get_logger(__name__)


class OTelBackend(ObservabilityBackend):
    """Universal backend: OpenSearch for logs, Prometheus for metrics.

    Environment variables (via config.py):
        OPENSEARCH_URL          e.g. https://my-cluster.us-east-1.es.amazonaws.com
        OPENSEARCH_API_KEY      base64 encoded id:secret
        PROMETHEUS_URL          e.g. http://prometheus.internal:9090
        OTEL_LOG_INDEX_PATTERN  default: otel-logs-*
    """

    def __init__(self, config: dict | None = None) -> None:
        from tinker.config import settings

        cfg = config or {}
        self._os_url = (cfg.get("opensearch_url") or getattr(settings, "opensearch_url", None) or "").rstrip("/")
        raw_key = cfg.get("opensearch_api_key") or cfg.get("api_key")
        if not raw_key:
            sk = settings.elasticsearch_api_key
            raw_key = sk.get_secret_value() if sk else None
        self._os_key = raw_key
        self._prom_url = (cfg.get("prometheus_url") or getattr(settings, "prometheus_url", None) or "").rstrip("/")
        self._log_index = cfg.get("log_index") or getattr(settings, "otel_log_index_pattern", "otel-logs-*")

        self._os_headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._os_key:
            self._os_headers["Authorization"] = f"ApiKey {self._os_key}"

    # ── Logs via OpenSearch ───────────────────────────────────────────────────

    async def query_logs(
        self,
        service: str,
        query: str,
        start: datetime,
        end: datetime,
        limit: int = 100,
        resource_type: str | None = None,
    ) -> list[LogEntry]:
        """Query OpenSearch using OpenTelemetry semantic conventions.

        OTel log field mapping:
          service.name              → service identifier
          body                      → log message
          severity_text             → log level (INFO/ERROR/etc.)
          trace_id                  → trace correlation
          span_id                   → span correlation
          @timestamp / time_unix_nano → timestamp
        """
        if not self._os_url:
            raise RuntimeError("OpenSearch is not configured (OPENSEARCH_URL is not set)")

        dsl: dict[str, Any] = {
            "size": limit,
            "sort": [{"@timestamp": {"order": "desc"}}],
            "query": {
                "bool": {
                    "must": [
                        {
                            "bool": {
                                "should": [
                                    {"match": {"body": query}},
                                    {"query_string": {"query": query, "fields": ["body", "attributes.*"]}},
                                ]
                            }
                        },
                        {"term": {"resource.attributes.service.name": service}},
                    ],
                    "filter": [
                        {
                            "range": {
                                "@timestamp": {
                                    "gte": start.isoformat(),
                                    "lte": end.isoformat(),
                                }
                            }
                        }
                    ],
                }
            },
        }

        log.debug("otel.query_logs", service=service, index=self._log_index, query=dsl["query"])

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._os_url}/{self._log_index}/_search",
                json=dsl,
                headers=self._os_headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

        return [self._parse_otel_hit(h) for h in data["hits"]["hits"]]

    def _parse_otel_hit(self, hit: dict[str, Any]) -> LogEntry:
        src = hit.get("_source", {})
        raw_ts = src.get("@timestamp") or src.get("observed_time_unix_nano", "")
        try:
            ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            ts = datetime.now(timezone.utc)

        # OTel semantic conventions
        resource = src.get("resource", {}).get("attributes", {})
        attrs = src.get("attributes", {})

        return LogEntry(
            timestamp=ts,
            message=sanitize_log_content(str(src.get("body", ""))),
            level=str(src.get("severity_text", "INFO")).upper(),
            service=resource.get("service.name", ""),
            trace_id=str(src.get("trace_id", "")),
            span_id=str(src.get("span_id", "")),
            extra={k: str(v) for k, v in attrs.items()},
        )

    # ── Metrics via Prometheus ────────────────────────────────────────────────

    async def get_metrics(
        self,
        service: str,
        metric_name: str,
        start: datetime,
        end: datetime,
        dimensions: dict[str, str] | None = None,
        resource_type: str | None = None,
    ) -> list[MetricPoint]:
        """Query Prometheus using the HTTP API range query endpoint.

        Works with any Prometheus-compatible source:
          - Prometheus / Victoria Metrics / Thanos / Cortex / Mimir
          - CloudWatch via yet-another-cloudwatch-exporter
          - GCP via stackdriver-exporter
          - Datadog via datadog-agent prometheus endpoint
        """
        if not self._prom_url:
            raise RuntimeError("Prometheus is not configured (PROMETHEUS_URL is not set)")

        # Build PromQL selector
        labels = {**(dimensions or {}), "service_name": service}
        label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
        promql = f'{metric_name}{{{label_str}}}'

        params = {
            "query": promql,
            "start": start.timestamp(),
            "end": end.timestamp(),
            "step": "60s",
        }

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._prom_url}/api/v1/query_range",
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

        points: list[MetricPoint] = []
        for result in data.get("data", {}).get("result", []):
            for ts_str, val_str in result.get("values", []):
                points.append(
                    MetricPoint(
                        timestamp=datetime.fromtimestamp(float(ts_str), tz=timezone.utc),
                        value=float(val_str),
                        unit=result.get("metric", {}).get("__name__", ""),
                        dimensions=result.get("metric", {}),
                    )
                )
        return points

    # ── Anomaly detection ─────────────────────────────────────────────────────

    async def detect_anomalies(
        self,
        service: str,
        window_minutes: int = 10,
    ) -> list[Anomaly]:
        from datetime import timedelta

        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=window_minutes)
        anomalies: list[Anomaly] = []

        # 1. Error log spike via OpenSearch
        try:
            error_logs = await self.query_logs(
                service,
                "severity_text:(ERROR OR CRITICAL OR FATAL)",
                start,
                end,
                limit=200,
            )
            if len(error_logs) > 10:
                representative, summary = self._summarize_logs(error_logs, window_minutes)
                anomalies.append(
                    Anomaly(
                        service=service,
                        metric="log_error_count",
                        description=f"{len(error_logs)} error logs in {window_minutes}m",
                        severity="high" if len(error_logs) > 50 else "medium",
                        current_value=float(len(error_logs)),
                        threshold=10.0,
                        recent_logs=representative,
                        log_summary=summary,
                    )
                )
        except Exception:
            log.exception("otel.anomaly.log_check_failed", service=service)

        # 2. HTTP 5xx rate via Prometheus (OTel http.server.request.duration metric)
        try:
            points = await self.get_metrics(
                service,
                "http_server_requests_total",
                start,
                end,
                dimensions={"status_code": "5.."},
            )
            if points:
                recent_errors = sum(p.value for p in points[-3:])
                if recent_errors > 20:
                    anomalies.append(
                        Anomaly(
                            service=service,
                            metric="http_5xx_rate",
                            description=f"HTTP 5xx rate elevated: {recent_errors:.0f} in last 3min",
                            severity="critical" if recent_errors > 100 else "high",
                            current_value=recent_errors,
                            threshold=20.0,
                        )
                    )
        except Exception:
            log.exception("otel.anomaly.metric_check_failed", service=service)

        return anomalies

"""Datadog observability backend.

Covers:
  - Datadog Log Management API  (search with Datadog query syntax)
  - Datadog Metrics API         (timeseries query)
  - Datadog APM / Traces API    (trace search)

Authentication
--------------
Datadog uses two keys:
  DATADOG_API_KEY   — identifies your organisation (required for all calls)
  DATADOG_APP_KEY   — grants access to read APIs (required for logs + metrics)

Both are long-lived secrets — store them in your secrets manager.
In AWS: AWS Secrets Manager → ECS task env injection.
In GCP: Secret Manager → Cloud Run env injection.

There is no "no-credential" option for Datadog (it's a SaaS product).
Use a service account / bot user with minimum scope:
  - logs_read_data
  - metrics_read
  - apm_read

Required environment variables
-------------------------------
  DATADOG_API_KEY      (required)
  DATADOG_APP_KEY      (required)
  DATADOG_SITE         default: datadoghq.com
                       EU:      datadoghq.eu
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog

from tinker.backends.base import Anomaly, LogEntry, MetricPoint, ObservabilityBackend
from tinker.agent.guardrails import sanitize_log_content

log = structlog.get_logger(__name__)


class DatadogBackend(ObservabilityBackend):
    """Observability backend for Datadog (Logs + Metrics + APM)."""

    def __init__(self) -> None:
        from tinker.config import settings

        api_key = getattr(settings, "datadog_api_key", None)
        app_key = getattr(settings, "datadog_app_key", None)
        site = getattr(settings, "datadog_site", None) or "datadoghq.com"

        if not api_key or not app_key:
            raise RuntimeError(
                "DATADOG_API_KEY and DATADOG_APP_KEY are required for the Datadog backend."
            )

        self._base_url = f"https://api.{site}"
        self._headers = {
            "DD-API-KEY": api_key.get_secret_value() if hasattr(api_key, "get_secret_value") else str(api_key),
            "DD-APPLICATION-KEY": app_key.get_secret_value() if hasattr(app_key, "get_secret_value") else str(app_key),
            "Content-Type": "application/json",
        }

    # ── Logs via Datadog Log Management API v2 ────────────────────────────────

    async def query_logs(
        self,
        service: str,
        query: str,
        start: datetime,
        end: datetime,
        limit: int = 100,
        resource_type: str | None = None,
    ) -> list[LogEntry]:
        """Search Datadog logs using the Logs Search API v2.

        `query` is a Tinker unified query string (e.g. 'level:ERROR AND "timeout"').
        Raw Datadog queries (starting with '@' or 'service:') are passed through.
        `resource_type` is accepted for API consistency but is not used by Datadog.
        """
        log.debug("datadog.query_logs", service=service)

        if query.startswith("@") or query.startswith("service:"):
            # Raw Datadog query — pass through
            dd_query = query
        else:
            from tinker.query import parse_query, translate_for
            ast = parse_query(query)
            dd_query = translate_for("datadog", ast, service=service)

        payload: dict[str, Any] = {
            "filter": {
                "query": dd_query,
                "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            "sort": "timestamp",
            "page": {"limit": limit},
        }

        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = await client.post(
                f"{self._base_url}/api/v2/logs/events/search",
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

        return [self._parse_log_event(e) for e in data.get("data", [])]

    def _parse_log_event(self, event: dict[str, Any]) -> LogEntry:
        attrs = event.get("attributes", {})
        ts_raw = attrs.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            ts = datetime.now(timezone.utc)

        # Datadog status → standard level
        status_map = {
            "debug": "DEBUG", "info": "INFO", "warn": "WARN",
            "warning": "WARN", "error": "ERROR", "critical": "CRITICAL",
            "emergency": "CRITICAL", "alert": "CRITICAL",
        }
        status = str(attrs.get("status", "info")).lower()
        level = status_map.get(status, "INFO")

        tags: list[str] = attrs.get("tags", [])
        trace_id = attrs.get("trace_id") or ""
        span_id = attrs.get("span_id") or ""

        return LogEntry(
            timestamp=ts,
            message=sanitize_log_content(str(attrs.get("message", ""))),
            level=level,
            service=str(attrs.get("service", "")),
            trace_id=str(trace_id),
            span_id=str(span_id),
            extra={"tags": ", ".join(tags)},
        )

    # ── Metrics via Datadog Metrics API v1 ────────────────────────────────────

    async def get_metrics(
        self,
        service: str,
        metric_name: str,
        start: datetime,
        end: datetime,
        dimensions: dict[str, str] | None = None,
        resource_type: str | None = None,
    ) -> list[MetricPoint]:
        """Fetch a Datadog metric timeseries.

        metric_name can be a full Datadog metric name or a query expression:
          aws.lambda.errors
          avg:trace.flask.request{service:payments}
        """
        log.debug("datadog.get_metrics", service=service, metric=metric_name)

        # Build a Datadog metric query if a simple name was given
        if not any(c in metric_name for c in ["{", ":", "avg", "sum", "max"]):
            query = f"avg:{metric_name}{{service:{service}}}"
        else:
            query = metric_name

        params = {
            "from": int(start.timestamp()),
            "to": int(end.timestamp()),
            "query": query,
        }

        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = await client.get(
                f"{self._base_url}/api/v1/query",
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

        points: list[MetricPoint] = []
        for series in data.get("series", []):
            unit_list = series.get("unit", [{}])
            unit = (unit_list[0] or {}).get("name", "") if unit_list else ""
            for ts_float, val in series.get("pointlist", []):
                if val is None:
                    continue
                points.append(
                    MetricPoint(
                        timestamp=datetime.fromtimestamp(ts_float / 1000, tz=timezone.utc),
                        value=float(val),
                        unit=unit,
                    )
                )
        return points

    # ── Traces via Datadog APM API v1 ─────────────────────────────────────────

    async def search_traces(
        self,
        service: str,
        query: str = "status:error",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search Datadog APM traces (bonus method, not in ABC)."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=1)

        payload: dict[str, Any] = {
            "filter": {
                "query": f"service:{service} {query}",
                "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            "page": {"limit": limit},
            "sort": "-timestamp",
        }

        async with httpx.AsyncClient(headers=self._headers) as client:
            resp = await client.post(
                f"{self._base_url}/api/v2/apm/traces",
                json=payload,
                timeout=30,
            )
            if resp.status_code == 404:
                return []  # APM not enabled
            resp.raise_for_status()
            return resp.json().get("data", [])

    # ── Anomaly detection ─────────────────────────────────────────────────────

    async def detect_anomalies(
        self,
        service: str,
        window_minutes: int = 10,
    ) -> list[Anomaly]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=window_minutes)
        anomalies: list[Anomaly] = []

        # 1. Error log count
        try:
            error_logs = await self.query_logs(
                service, "status:error OR status:critical", start, end, limit=200
            )
            if len(error_logs) > 10:
                anomalies.append(
                    Anomaly(
                        service=service,
                        metric="log_error_count",
                        description=f"{len(error_logs)} error logs in Datadog in {window_minutes}m",
                        severity="high" if len(error_logs) > 50 else "medium",
                        current_value=float(len(error_logs)),
                        threshold=10.0,
                        recent_logs=error_logs[:20],
                    )
                )
        except Exception:
            log.exception("datadog.anomaly.log_check_failed", service=service)

        # 2. Error rate metric
        try:
            points = await self.get_metrics(
                service,
                f"avg:trace.{service}.request.errors{{service:{service}}}",
                start,
                end,
            )
            if points:
                recent_avg = sum(p.value for p in points[-3:]) / max(len(points[-3:]), 1)
                if recent_avg > 5:
                    anomalies.append(
                        Anomaly(
                            service=service,
                            metric="trace_error_rate",
                            description=f"Trace error rate: {recent_avg:.1f}/min (threshold: 5)",
                            severity="critical" if recent_avg > 50 else "high",
                            current_value=recent_avg,
                            threshold=5.0,
                        )
                    )
        except Exception:
            log.exception("datadog.anomaly.metric_check_failed", service=service)

        return anomalies

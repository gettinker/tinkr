"""Grafana Stack backend: Loki (logs) + Prometheus (metrics) + Tempo (traces).

This backend covers both:
  - Self-hosted Grafana OSS stack (Loki + Prometheus + Tempo)
  - Grafana Cloud (same APIs, different base URLs + API key auth)

All three components are queried via their HTTP APIs — no SDKs needed.

Authentication
--------------
Self-hosted (no auth):
  GRAFANA_LOKI_URL=http://loki:3100
  GRAFANA_PROMETHEUS_URL=http://prometheus:9090
  GRAFANA_TEMPO_URL=http://tempo:3200

Grafana Cloud (API key auth):
  GRAFANA_LOKI_URL=https://<id>.grafana.net/loki
  GRAFANA_PROMETHEUS_URL=https://<id>.grafana.net/prometheus
  GRAFANA_TEMPO_URL=https://<id>.grafana.net/tempo
  GRAFANA_API_KEY=glc_...   (Grafana Cloud API key)

Basic auth (self-hosted with auth enabled):
  GRAFANA_USER=admin
  GRAFANA_PASSWORD=...   (use a secrets manager in prod)

Any cloud provider can feed this stack:
  AWS   → CloudWatch → Grafana Agent → Loki / Prometheus
  GCP   → Cloud Logging / Monitoring → Grafana Agent → Loki / Prometheus
  Azure → Azure Monitor → Grafana Agent → Loki / Prometheus
  K8s   → Prometheus Operator + Promtail → native
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog

from tinker.backends.base import Anomaly, LogEntry, MetricPoint, ObservabilityBackend
from tinker.agent.guardrails import sanitize_log_content

log = structlog.get_logger(__name__)


def _ns_to_dt(ns: int | str) -> datetime:
    """Convert nanosecond unix timestamp to datetime."""
    return datetime.fromtimestamp(int(ns) / 1e9, tz=timezone.utc)


class GrafanaBackend(ObservabilityBackend):
    """Observability backend for the Grafana stack (Loki + Prometheus + Tempo)."""

    def __init__(self) -> None:
        from tinker.config import settings

        self._loki_url = getattr(settings, "grafana_loki_url", "") or ""
        self._prom_url = getattr(settings, "grafana_prometheus_url", "") or ""
        self._tempo_url = getattr(settings, "grafana_tempo_url", "") or ""

        # Auth: API key takes precedence over basic auth
        api_key = getattr(settings, "grafana_api_key", None)
        user = getattr(settings, "grafana_user", None)
        password = getattr(settings, "grafana_password", None)

        self._auth: httpx.Auth | None = None
        self._headers: dict[str, str] = {}

        if api_key:
            secret = api_key.get_secret_value() if hasattr(api_key, "get_secret_value") else str(api_key)
            self._headers["Authorization"] = f"Bearer {secret}"
        elif user and password:
            pwd = password.get_secret_value() if hasattr(password, "get_secret_value") else str(password)
            self._auth = httpx.BasicAuth(user, pwd)

    # ── Logs via Loki ─────────────────────────────────────────────────────────

    async def query_logs(
        self,
        service: str,
        query: str,
        start: datetime,
        end: datetime,
        limit: int = 100,
    ) -> list[LogEntry]:
        """Query Loki using LogQL.

        If `query` is a plain string (no LogQL syntax), we wrap it as:
          {service_name="<service>"} |= "<query>"
        Otherwise it is passed through as-is (full LogQL expression).
        """
        if not self._loki_url:
            log.warning("grafana.loki_not_configured")
            return []

        if not query.startswith("{"):
            logql = f'{{service_name="{service}"}} |= `{query}`'
        else:
            logql = query

        params = {
            "query": logql,
            "start": str(int(start.timestamp() * 1e9)),  # nanoseconds
            "end": str(int(end.timestamp() * 1e9)),
            "limit": str(limit),
            "direction": "backward",
        }

        async with httpx.AsyncClient(auth=self._auth, headers=self._headers) as client:
            resp = await client.get(
                f"{self._loki_url}/loki/api/v1/query_range",
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

        entries: list[LogEntry] = []
        for stream in data.get("data", {}).get("result", []):
            stream_labels: dict[str, str] = stream.get("stream", {})
            svc = stream_labels.get("service_name") or stream_labels.get("app") or service
            level = (
                stream_labels.get("level")
                or stream_labels.get("severity")
                or "INFO"
            ).upper()
            for ts_ns, line in stream.get("values", []):
                entries.append(
                    LogEntry(
                        timestamp=_ns_to_dt(ts_ns),
                        message=sanitize_log_content(line),
                        level=level,
                        service=svc,
                        extra=stream_labels,
                    )
                )

        return entries

    # ── Metrics via Prometheus ────────────────────────────────────────────────

    async def get_metrics(
        self,
        service: str,
        metric_name: str,
        start: datetime,
        end: datetime,
        dimensions: dict[str, str] | None = None,
    ) -> list[MetricPoint]:
        """Query Prometheus range query API."""
        if not self._prom_url:
            log.warning("grafana.prometheus_not_configured")
            return []

        labels = {**(dimensions or {}), "service_name": service}
        label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
        promql = f'{metric_name}{{{label_str}}}'

        params = {
            "query": promql,
            "start": start.timestamp(),
            "end": end.timestamp(),
            "step": "60s",
        }

        async with httpx.AsyncClient(auth=self._auth, headers=self._headers) as client:
            resp = await client.get(
                f"{self._prom_url}/api/v1/query_range",
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

        points: list[MetricPoint] = []
        for result in data.get("data", {}).get("result", []):
            metric_labels: dict[str, str] = result.get("metric", {})
            for ts_float, val_str in result.get("values", []):
                try:
                    points.append(
                        MetricPoint(
                            timestamp=datetime.fromtimestamp(float(ts_float), tz=timezone.utc),
                            value=float(val_str),
                            unit=metric_labels.get("__name__", ""),
                            dimensions=metric_labels,
                        )
                    )
                except (ValueError, TypeError):
                    continue

        return points

    # ── Traces via Tempo ──────────────────────────────────────────────────────

    async def search_traces(
        self,
        service: str,
        tags: dict[str, str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search Tempo for recent traces for a service (bonus, not in ABC)."""
        if not self._tempo_url:
            return []

        params: dict[str, Any] = {
            "tags": f'service.name="{service}"',
            "limit": limit,
        }
        if tags:
            extra = " ".join(f'{k}="{v}"' for k, v in tags.items())
            params["tags"] = f'{params["tags"]} {extra}'

        async with httpx.AsyncClient(auth=self._auth, headers=self._headers) as client:
            resp = await client.get(
                f"{self._tempo_url}/api/search",
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json().get("traces", [])

    # ── Anomaly detection ─────────────────────────────────────────────────────

    async def detect_anomalies(
        self,
        service: str,
        window_minutes: int = 10,
    ) -> list[Anomaly]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=window_minutes)
        anomalies: list[Anomaly] = []

        # 1. Error log count via Loki
        try:
            error_logs = await self.query_logs(
                service,
                f'{{service_name="{service}"}} |= `error` | logfmt | level =~ "error|ERROR|critical|CRITICAL"',
                start,
                end,
                limit=200,
            )
            if len(error_logs) > 10:
                anomalies.append(
                    Anomaly(
                        service=service,
                        metric="loki_error_count",
                        description=f"{len(error_logs)} error lines in Loki in {window_minutes}m",
                        severity="high" if len(error_logs) > 50 else "medium",
                        current_value=float(len(error_logs)),
                        threshold=10.0,
                        recent_logs=error_logs[:20],
                    )
                )
        except Exception:
            log.exception("grafana.anomaly.loki_check_failed", service=service)

        # 2. HTTP 5xx rate via Prometheus
        try:
            points = await self.get_metrics(
                service,
                "http_requests_total",
                start,
                end,
                dimensions={"status": "5.."},
            )
            if points:
                recent = sum(p.value for p in points[-3:])
                if recent > 20:
                    anomalies.append(
                        Anomaly(
                            service=service,
                            metric="http_5xx_rate",
                            description=f"HTTP 5xx elevated: {recent:.0f} in last 3 data points",
                            severity="critical" if recent > 100 else "high",
                            current_value=recent,
                            threshold=20.0,
                        )
                    )
        except Exception:
            log.exception("grafana.anomaly.prom_check_failed", service=service)

        return anomalies

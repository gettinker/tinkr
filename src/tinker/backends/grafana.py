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
import json
from typing import Any, AsyncGenerator

import httpx
import structlog

from tinker.backends.base import Anomaly, LogEntry, MetricPoint, ObservabilityBackend, ServiceNotFoundError
from tinker.agent.guardrails import sanitize_log_content

log = structlog.get_logger(__name__)


def _ns_to_dt(ns: int | str) -> datetime:
    """Convert nanosecond unix timestamp to datetime."""
    return datetime.fromtimestamp(int(ns) / 1e9, tz=timezone.utc)


class GrafanaBackend(ObservabilityBackend):
    """Observability backend for the Grafana stack (Loki + Prometheus + Tempo)."""

    def __init__(self, config: dict | None = None) -> None:
        from tinker.config import settings

        cfg = config or {}
        self._loki_url = cfg.get("loki_url") or getattr(settings, "grafana_loki_url", "") or ""
        self._prom_url = cfg.get("prometheus_url") or getattr(settings, "grafana_prometheus_url", "") or ""
        self._tempo_url = cfg.get("tempo_url") or getattr(settings, "grafana_tempo_url", "") or ""
        self._service_label: str = cfg.get("service_label") or getattr(settings, "grafana_service_label", "service") or "service"
        self._log_format: str = cfg.get("log_format") or getattr(settings, "grafana_log_format", "label") or "label"

        # Per-service format cache populated by _detect_log_format().
        self._format_cache: dict[str, str] = {}

        # Auth: API key takes precedence over basic auth
        api_key = cfg.get("api_key") or getattr(settings, "grafana_api_key", None)
        user = cfg.get("user") or getattr(settings, "grafana_user", None)
        password = cfg.get("password") or getattr(settings, "grafana_password", None)

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
        resource_type: str | None = None,
    ) -> list[LogEntry]:
        """Query Loki using LogQL.

        `query` is a Tinker unified query string (e.g. 'level:ERROR AND "timeout"').
        `resource_type` adds infrastructure labels to the Loki stream selector.
        Raw LogQL (starting with '{') is passed through unchanged.
        """
        if not self._loki_url:
            raise RuntimeError("Loki is not configured (GRAFANA_LOKI_URL is not set)")

        if query.startswith("{"):
            # Raw LogQL — pass through unchanged
            logql = query
        else:
            from tinker.query import parse_query, translate_for
            ast = parse_query(query)
            logql = translate_for("grafana", ast, service=service, resource_type=resource_type, service_label=self._service_label)

        params = {
            "query": logql,
            "start": str(int(start.timestamp() * 1e9)),  # nanoseconds
            "end": str(int(end.timestamp() * 1e9)),
            "limit": str(limit),
            "direction": "backward",
        }

        log.debug("grafana.loki_query", logql=logql, start=start.isoformat(), end=end.isoformat())

        async with httpx.AsyncClient(auth=self._auth, headers=self._headers) as client:
            resp = await client.get(
                f"{self._loki_url}/loki/api/v1/query_range",
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

        result_streams = data.get("data", {}).get("result", [])
        log.debug("grafana.loki_response", streams=len(result_streams), status=data.get("status"))

        if not result_streams:
            # Distinguish "no logs in this window" from "service label never seen by Loki"
            if not await self._loki_service_exists(service):
                raise ServiceNotFoundError(service, backend="Loki")
            return []

        entries: list[LogEntry] = []
        for stream in result_streams:
            stream_labels: dict[str, str] = stream.get("stream", {})
            svc = stream_labels.get("service") or stream_labels.get("service_name") or stream_labels.get("app") or service
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

    async def _loki_service_exists(self, service: str) -> bool:
        """Check if a service label value exists in Loki.

        Uses the label values API — fast, no log data fetched.
        Returns True if the service is known to Loki (even if it has no recent logs).
        """
        try:
            async with httpx.AsyncClient(auth=self._auth, headers=self._headers) as client:
                resp = await client.get(
                    f"{self._loki_url}/loki/api/v1/label/{self._service_label}/values",
                    timeout=10,
                )
                resp.raise_for_status()
                values: list[str] = resp.json().get("data", [])
            return service in values
        except Exception as exc:
            # If the label check itself fails, log and assume service may exist
            # to avoid false 404s when Loki is degraded.
            log.warning("grafana.loki_label_check_failed", service=service, error=str(exc))
            return True

    # ── Loki native tail (websocket) ──────────────────────────────────────────

    async def tail_logs(
        self,
        service: str,
        query: str = "*",
        poll_interval: float = 2.0,
        resource_type: str | None = None,
    ) -> AsyncGenerator[LogEntry, None]:
        """Stream new log entries using Loki's websocket tail API.

        Falls back to poll-based tailing if websockets are unavailable.
        """
        if not self._loki_url:
            log.warning("grafana.loki_not_configured — falling back to poll tail")
            async for entry in super().tail_logs(service, query, poll_interval, resource_type=resource_type):
                yield entry
            return

        from tinker.query import parse_query, translate_for
        ast = parse_query(query)
        logql = translate_for("grafana", ast, service=service, resource_type=resource_type)

        # Convert http(s):// to ws(s):// for the websocket endpoint
        ws_base = self._loki_url.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{ws_base}/loki/api/v1/tail"

        params = {"query": logql, "delay_for": "0", "limit": "50"}
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        full_url = f"{ws_url}?{qs}"

        try:
            import websockets  # type: ignore[import]
        except ImportError:
            log.warning("grafana.websockets_not_installed — falling back to poll tail")
            async for entry in super().tail_logs(service, query, poll_interval):
                yield entry
            return

        extra_headers = {}
        if self._headers.get("Authorization"):
            extra_headers["Authorization"] = self._headers["Authorization"]

        try:
            async with websockets.connect(full_url, additional_headers=extra_headers) as ws:
                log.debug("grafana.tail.connected", url=full_url)
                async for raw in ws:
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    for stream in data.get("streams", []):
                        stream_labels: dict[str, str] = stream.get("stream", {})
                        level = (
                            stream_labels.get("level")
                            or stream_labels.get("severity")
                            or "INFO"
                        ).upper()
                        svc = (
                            stream_labels.get("service")
                            or stream_labels.get("service_name")
                            or service
                        )
                        for ts_ns, line in stream.get("values", []):
                            yield LogEntry(
                                timestamp=_ns_to_dt(ts_ns),
                                message=sanitize_log_content(line),
                                level=level,
                                service=svc,
                                extra=stream_labels,
                            )
        except Exception as exc:
            log.warning("grafana.tail.websocket_failed", error=str(exc))
            log.info("grafana.tail.fallback_to_poll")
            async for entry in super().tail_logs(service, query, poll_interval, resource_type=resource_type):
                yield entry

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
        """Query Prometheus range query API."""
        if not self._prom_url:
            raise RuntimeError("Prometheus is not configured (GRAFANA_PROMETHEUS_URL is not set)")

        labels = {**(dimensions or {}), "job": service}
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
        backend_errors: list[str] = []

        # 1. Error log count via Loki
        try:
            error_logs = await self.query_logs(
                service,
                f'{{service="{service}"}} |= `error` | logfmt | level =~ "error|ERROR|critical|CRITICAL"',
                start,
                end,
                limit=200,
            )
            if len(error_logs) > 10:
                representative, summary = self._summarize_logs(error_logs, window_minutes)
                anomalies.append(
                    Anomaly(
                        service=service,
                        metric="loki_error_count",
                        description=f"{len(error_logs)} error lines in Loki in {window_minutes}m",
                        severity="high" if len(error_logs) > 50 else "medium",
                        current_value=float(len(error_logs)),
                        threshold=10.0,
                        recent_logs=representative,
                        log_summary=summary,
                    )
                )
        except Exception as exc:
            log.exception("grafana.anomaly.loki_check_failed", service=service)
            backend_errors.append(f"Loki: {exc}")

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
        except Exception as exc:
            log.exception("grafana.anomaly.prom_check_failed", service=service)
            backend_errors.append(f"Prometheus: {exc}")

        # Surface errors to the caller if no anomalies were found — otherwise the
        # user sees an empty table with no indication that the backend is unhealthy.
        if backend_errors and not anomalies:
            raise RuntimeError(
                "Observability backend check(s) failed — "
                + "; ".join(backend_errors)
            )

        return anomalies

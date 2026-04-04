"""Remote client — talks to a deployed Tinker server over HTTP.

The server holds cloud credentials and the LLM key.
The CLI only needs the server URL and an API token.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, AsyncGenerator

import httpx
import structlog

from tinker.backends.base import Anomaly, LogEntry, MetricPoint
from tinker.agent.orchestrator import IncidentReport
from tinker.client.config import ServerConfig

log = structlog.get_logger(__name__)

# How long to wait for a server response on query operations
_QUERY_TIMEOUT = 30.0
_ANALYZE_TIMEOUT = 300.0  # analysis can take a while


def _parse_log_entry(d: dict) -> LogEntry:
    from datetime import timezone
    ts_raw = d.get("timestamp", "")
    try:
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        ts = datetime.now(timezone.utc)
    return LogEntry(
        timestamp=ts,
        message=d.get("message", ""),
        level=d.get("level", "INFO"),
        service=d.get("service", ""),
        trace_id=d.get("trace_id", ""),
        span_id=d.get("span_id", ""),
        extra=d.get("extra", {}),
    )


def _parse_metric_point(d: dict) -> MetricPoint:
    from datetime import timezone
    ts_raw = d.get("timestamp", "")
    try:
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        ts = datetime.now(timezone.utc)
    return MetricPoint(
        timestamp=ts,
        value=float(d.get("value", 0)),
        unit=d.get("unit", ""),
        dimensions=d.get("dimensions", {}),
    )


class RemoteClient:
    """Routes all operations through the Tinker server REST API."""

    mode = "server"

    def __init__(self, cfg: ServerConfig) -> None:
        self._cfg = cfg
        self._base = cfg.url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._cfg.api_key}"}

    def _client(self, timeout: float = _QUERY_TIMEOUT) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base,
            headers=self._headers(),
            timeout=timeout,
        )

    # ── Observability ─────────────────────────────────────────────────────────

    async def query_logs(
        self,
        service: str,
        query: str,
        start: datetime,
        end: datetime,
        limit: int = 100,
        resource_type: str | None = None,
    ) -> list[LogEntry]:
        body: dict = {
            "service": service,
            "query": query,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": limit,
        }
        if resource_type:
            body["resource_type"] = resource_type
        async with self._client() as c:
            resp = await c.post("/api/v1/logs", json=body)
            resp.raise_for_status()
        return [_parse_log_entry(e) for e in resp.json().get("entries", [])]

    async def tail_logs(
        self,
        service: str,
        query: str = "*",
        poll_interval: float = 2.0,
        resource_type: str | None = None,
    ) -> AsyncGenerator[LogEntry, None]:
        """Poll the server's query endpoint for new entries."""
        import asyncio
        from datetime import timedelta, timezone

        seen: set[tuple[datetime, str]] = set()
        cursor = datetime.now(timezone.utc) - timedelta(seconds=5)

        while True:
            now = datetime.now(timezone.utc)
            try:
                entries = await self.query_logs(service, query, cursor, now, limit=200, resource_type=resource_type)
                for entry in sorted(entries, key=lambda e: e.timestamp):
                    key = (entry.timestamp, entry.message)
                    if key not in seen:
                        seen.add(key)
                        cursor = max(cursor, entry.timestamp)
                        yield entry
            except Exception as exc:
                log.warning("remote.tail.poll_failed", error=str(exc))
            await asyncio.sleep(poll_interval)

    async def get_metrics(
        self,
        service: str,
        metric_name: str,
        start: datetime,
        end: datetime,
        resource_type: str | None = None,
    ) -> list[MetricPoint]:
        body: dict = {
            "service": service,
            "metric": metric_name,
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
        if resource_type:
            body["resource_type"] = resource_type
        async with self._client() as c:
            resp = await c.post("/api/v1/metrics", json=body)
            resp.raise_for_status()
        return [_parse_metric_point(p) for p in resp.json().get("points", [])]

    async def detect_anomalies(
        self,
        service: str,
        window_minutes: int = 10,
    ) -> list[Anomaly]:
        from datetime import timezone
        async with self._client() as c:
            resp = await c.post("/api/v1/anomalies", json={
                "service": service,
                "window_minutes": window_minutes,
            })
            resp.raise_for_status()
        results = []
        for d in resp.json().get("anomalies", []):
            ts_raw = d.get("detected_at", "")
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                ts = datetime.now(timezone.utc)
            results.append(Anomaly(
                service=d["service"],
                metric=d["metric"],
                description=d["description"],
                severity=d["severity"],
                detected_at=ts,
                current_value=float(d.get("current_value", 0)),
                threshold=float(d.get("threshold", 0)),
            ))
        return results

    # ── Agent ─────────────────────────────────────────────────────────────────

    async def analyze(
        self,
        service: str,
        since: str,
        deep: bool = False,
    ) -> IncidentReport:
        """Collect the full report (non-streaming)."""
        report_data: dict | None = None
        async for chunk in self.stream_analyze(service, since, deep):
            # Drain the stream — report arrives as the last "report" event
            if isinstance(chunk, dict):
                report_data = chunk

        if report_data is None:
            raise RuntimeError("Server did not return an incident report")

        from datetime import timezone
        ts_raw = report_data.get("created_at", "")
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            ts = datetime.now(timezone.utc)

        return IncidentReport(
            incident_id=report_data["incident_id"],
            service=report_data["service"],
            severity=report_data["severity"],
            root_cause=report_data["root_cause"],
            summary=report_data.get("summary", ""),
            affected_services=report_data.get("affected_services", []),
            suggested_fix=report_data.get("suggested_fix"),
            model_used=report_data.get("model_used", ""),
            created_at=ts,
        )

    async def stream_analyze(
        self,
        service: str,
        since: str,
        deep: bool = False,
    ) -> AsyncGenerator[str, None]:
        """Stream text chunks from the server SSE endpoint.

        Yields str for text chunks and dict for the final report event.
        """
        async with httpx.AsyncClient(
            base_url=self._base,
            headers=self._headers(),
            timeout=_ANALYZE_TIMEOUT,
        ) as c:
            async with c.stream(
                "POST",
                "/api/v1/analyze",
                json={"service": service, "since": since, "deep": deep},
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    event_type = data.get("_event")  # injected by client below
                    if "text" in data:
                        yield data["text"]
                    elif "incident_id" in data:
                        yield data  # type: ignore[misc]  — final report dict

    async def get_fix(self, incident_id: str) -> dict[str, Any]:
        async with self._client() as c:
            resp = await c.post("/api/v1/fix", json={"incident_id": incident_id})
            resp.raise_for_status()
        return resp.json()

    # ── Watches ───────────────────────────────────────────────────────────────

    async def create_watch(
        self,
        service: str,
        slack_channel: str | None = None,
        interval_seconds: int = 60,
    ) -> dict[str, Any]:
        body: dict = {"service": service, "interval_seconds": interval_seconds}
        if slack_channel:
            body["slack_channel"] = slack_channel
        async with self._client() as c:
            resp = await c.post("/api/v1/watches", json=body)
            resp.raise_for_status()
        return resp.json()

    async def list_watches(self) -> list[dict[str, Any]]:
        async with self._client() as c:
            resp = await c.get("/api/v1/watches")
            resp.raise_for_status()
        return resp.json().get("watches", [])

    async def stop_watch(self, watch_id: str) -> dict[str, Any]:
        async with self._client() as c:
            resp = await c.delete(f"/api/v1/watches/{watch_id}")
            resp.raise_for_status()
        return resp.json()

    # ── Ops ───────────────────────────────────────────────────────────────────

    async def health(self) -> dict[str, Any]:
        async with self._client(timeout=5.0) as c:
            resp = await c.get("/health")
            resp.raise_for_status()
        return resp.json()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def parse_since(self, since: str) -> "datetime":
        from datetime import timedelta, timezone
        now = datetime.now(timezone.utc)
        unit = since[-1]
        value = int(since[:-1])
        match unit:
            case "m": return now - timedelta(minutes=value)
            case "h": return now - timedelta(hours=value)
            case "d": return now - timedelta(days=value)
            case _: raise ValueError(f"Unknown time unit '{unit}' in '{since}'. Use m/h/d.")

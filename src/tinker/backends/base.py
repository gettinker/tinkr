"""Abstract base class for all observability backends."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator


# ── Data models ───────────────────────────────────────────────────────────────


@dataclass
class LogEntry:
    timestamp: datetime
    message: str
    level: str = "INFO"
    service: str = ""
    trace_id: str = ""
    span_id: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    def is_error(self) -> bool:
        return self.level.upper() in {"ERROR", "CRITICAL", "FATAL"}


@dataclass
class MetricPoint:
    timestamp: datetime
    value: float
    unit: str = ""
    dimensions: dict[str, str] = field(default_factory=dict)


@dataclass
class Anomaly:
    service: str
    metric: str
    description: str
    severity: str  # "low" | "medium" | "high" | "critical"
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    current_value: float = 0.0
    threshold: float = 0.0
    recent_logs: list[LogEntry] = field(default_factory=list)
    # Pre-computed compact summary used by explain/fix — avoids sending all raw logs to LLM.
    # Built by LogSummarizer at detection time. Contains unique_patterns, stack_traces,
    # time_distribution, common_fields.
    log_summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "service": self.service,
            "metric": self.metric,
            "description": self.description,
            "severity": self.severity,
            "detected_at": self.detected_at.isoformat(),
            "current_value": self.current_value,
            "threshold": self.threshold,
            "log_summary": self.log_summary,
        }


# ── Abstract backend ──────────────────────────────────────────────────────────


class ObservabilityBackend(ABC):
    """Common interface for all observability providers."""

    @abstractmethod
    async def query_logs(
        self,
        service: str,
        query: str,
        start: datetime,
        end: datetime,
        limit: int = 100,
        resource_type: str | None = None,
    ) -> list[LogEntry]:
        """Run a provider-specific log query and return normalised entries.

        Args:
            service:       Service / function / container name.
            query:         Tinker unified query string (e.g. 'level:ERROR AND "timeout"').
            resource_type: Infrastructure type — controls log group / table / index routing.
                           Examples: "ecs", "lambda", "eks", "rds", "cloudrun", "aks".
                           None → auto-discover or use backend default.
        """
        ...

    @abstractmethod
    async def get_metrics(
        self,
        service: str,
        metric_name: str,
        start: datetime,
        end: datetime,
        dimensions: dict[str, str] | None = None,
        resource_type: str | None = None,
    ) -> list[MetricPoint]:
        """Fetch metric time-series data."""
        ...

    @abstractmethod
    async def detect_anomalies(
        self,
        service: str,
        window_minutes: int = 10,
    ) -> list[Anomaly]:
        """Detect anomalies for a service over a recent time window."""
        ...

    # ── Streaming (non-abstract, poll-based default) ──────────────────────────

    async def tail_logs(
        self,
        service: str,
        query: str = "*",
        poll_interval: float = 2.0,
        resource_type: str | None = None,
    ) -> AsyncGenerator[LogEntry, None]:
        """Stream new log entries as they arrive.

        Default implementation polls query_logs every *poll_interval* seconds,
        deduplicating by timestamp. Backends that support native streaming
        (e.g. Loki websocket) should override this.
        """
        seen: set[tuple[datetime, str]] = set()
        cursor = datetime.now(timezone.utc) - timedelta(seconds=5)

        while True:
            now = datetime.now(timezone.utc)
            entries = await self.query_logs(
                service, query, cursor, now, limit=200, resource_type=resource_type
            )
            new_cursor = cursor
            for entry in sorted(entries, key=lambda e: e.timestamp):
                key = (entry.timestamp, entry.message)
                if key not in seen:
                    seen.add(key)
                    new_cursor = max(new_cursor, entry.timestamp)
                    yield entry
            cursor = new_cursor
            await asyncio.sleep(poll_interval)

    # ── Log summarisation helper ──────────────────────────────────────────────

    def _summarize_logs(
        self,
        logs: list[LogEntry],
        window_minutes: int = 10,
    ) -> tuple[list[LogEntry], dict]:
        """Deduplicate *logs* into representative samples + a compact summary dict.

        Returns ``(representative_logs, log_summary)`` where:
          - ``representative_logs`` is one example per unique error pattern (≤ 10),
            preferring entries that contain a stack trace.
          - ``log_summary`` is a compact dict (~300–500 tokens) with unique_patterns,
            stack_traces, time_distribution, and common_fields.

        Call this from ``detect_anomalies`` before building the Anomaly object so
        that the LLM never receives thousands of raw identical log lines.
        """
        from tinker.monitor.summarizer import LogSummarizer
        return LogSummarizer().summarize(logs, window_minutes=window_minutes)

    # ── Convenience helpers (non-abstract) ────────────────────────────────────

    async def get_recent_errors(
        self,
        service: str,
        minutes: int = 30,
        limit: int = 50,
    ) -> list[LogEntry]:
        """Return recent ERROR-level log entries for a service."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=minutes)
        entries = await self.query_logs(service, "level:ERROR OR level:CRITICAL", start, end, limit)
        return [e for e in entries if e.is_error()]

    def _parse_since(self, since: str) -> datetime:
        """Parse a human-friendly duration string into an absolute start time.

        Examples: '1h', '30m', '2d'
        """
        now = datetime.now(timezone.utc)
        unit = since[-1]
        value = int(since[:-1])
        match unit:
            case "m":
                return now - timedelta(minutes=value)
            case "h":
                return now - timedelta(hours=value)
            case "d":
                return now - timedelta(days=value)
            case _:
                raise ValueError(f"Unknown time unit '{unit}' in '{since}'. Use m/h/d.")

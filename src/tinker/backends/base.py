"""Abstract base class for all observability backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


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

    def to_dict(self) -> dict[str, object]:
        return {
            "service": self.service,
            "metric": self.metric,
            "description": self.description,
            "severity": self.severity,
            "detected_at": self.detected_at.isoformat(),
            "current_value": self.current_value,
            "threshold": self.threshold,
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
    ) -> list[LogEntry]:
        """Run a provider-specific log query and return normalised entries."""
        ...

    @abstractmethod
    async def get_metrics(
        self,
        service: str,
        metric_name: str,
        start: datetime,
        end: datetime,
        dimensions: dict[str, str] | None = None,
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

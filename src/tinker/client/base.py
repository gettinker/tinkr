"""Abstract base for LocalClient and RemoteClient."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, AsyncGenerator

from tinker.backends.base import Anomaly, LogEntry, MetricPoint
from tinker.agent.orchestrator import IncidentReport


class TinkerClient(ABC):
    """Unified interface used by the CLI regardless of local vs server mode."""

    mode: str  # "local" | "server"

    # ── Observability ─────────────────────────────────────────────────────────

    @abstractmethod
    async def query_logs(
        self,
        service: str,
        query: str,
        start: datetime,
        end: datetime,
        limit: int = 100,
        resource_type: str | None = None,
    ) -> list[LogEntry]: ...

    @abstractmethod
    async def tail_logs(
        self,
        service: str,
        query: str = "*",
        poll_interval: float = 2.0,
        resource_type: str | None = None,
    ) -> AsyncGenerator[LogEntry, None]: ...

    @abstractmethod
    async def get_metrics(
        self,
        service: str,
        metric_name: str,
        start: datetime,
        end: datetime,
        resource_type: str | None = None,
    ) -> list[MetricPoint]: ...

    @abstractmethod
    async def detect_anomalies(
        self,
        service: str,
        window_minutes: int = 10,
    ) -> list[Anomaly]: ...

    # ── Agent ─────────────────────────────────────────────────────────────────

    @abstractmethod
    async def analyze(
        self,
        service: str,
        since: str,
        deep: bool = False,
    ) -> IncidentReport: ...

    @abstractmethod
    async def stream_analyze(
        self,
        service: str,
        since: str,
        deep: bool = False,
    ) -> AsyncGenerator[str, None]: ...

    @abstractmethod
    async def get_fix(self, incident_id: str) -> dict[str, Any]: ...

    # ── Ops ───────────────────────────────────────────────────────────────────

    @abstractmethod
    async def health(self) -> dict[str, Any]: ...

    # ── Helpers (shared) ──────────────────────────────────────────────────────

    def parse_since(self, since: str) -> datetime:
        from datetime import timedelta, timezone
        now = datetime.now(timezone.utc)
        unit = since[-1]
        value = int(since[:-1])
        match unit:
            case "m": return now - timedelta(minutes=value)
            case "h": return now - timedelta(hours=value)
            case "d": return now - timedelta(days=value)
            case _: raise ValueError(f"Unknown time unit '{unit}' in '{since}'. Use m/h/d.")

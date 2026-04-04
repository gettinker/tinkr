"""Local client — talks directly to the cloud backend from the CLI machine.

Credentials are whatever the SDK credential chain finds locally:
  AWS:   aws sso login / ~/.aws/credentials / env vars
  GCP:   gcloud auth application-default login
  Azure: az login
  Other: env vars (DATADOG_API_KEY etc.)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncGenerator

import structlog

from tinker.agent.orchestrator import AgentSession, IncidentReport, Orchestrator
from tinker.backends import get_backend
from tinker.backends.base import Anomaly, LogEntry, MetricPoint
from tinker.client.base import TinkerClient
from tinker.client.config import LocalConfig

log = structlog.get_logger(__name__)


class LocalClient(TinkerClient):
    """Runs all operations on the local machine against the configured backend."""

    mode = "local"

    def __init__(self, cfg: LocalConfig) -> None:
        self._cfg = cfg
        self._backend = get_backend(cfg.backend)

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
        return await self._backend.query_logs(service, query, start, end, limit, resource_type=resource_type)

    async def tail_logs(
        self,
        service: str,
        query: str = "*",
        poll_interval: float = 2.0,
        resource_type: str | None = None,
    ) -> AsyncGenerator[LogEntry, None]:
        return self._backend.tail_logs(service, query, poll_interval, resource_type=resource_type)

    async def get_metrics(
        self,
        service: str,
        metric_name: str,
        start: datetime,
        end: datetime,
        resource_type: str | None = None,
    ) -> list[MetricPoint]:
        return await self._backend.get_metrics(service, metric_name, start, end, resource_type=resource_type)

    async def detect_anomalies(
        self,
        service: str,
        window_minutes: int = 10,
    ) -> list[Anomaly]:
        return await self._backend.detect_anomalies(service, window_minutes)

    # ── Agent ─────────────────────────────────────────────────────────────────

    def _make_orchestrator(self, deep: bool) -> Orchestrator:
        model = self._cfg.deep_rca_model if deep else self._cfg.default_model
        return Orchestrator(use_deep_rca=deep, model=model)

    async def analyze(
        self,
        service: str,
        since: str,
        deep: bool = False,
    ) -> IncidentReport:
        orch = self._make_orchestrator(deep)
        session = AgentSession(service=service)
        return await orch.analyze(service, since, session)

    async def stream_analyze(
        self,
        service: str,
        since: str,
        deep: bool = False,
    ) -> AsyncGenerator[str, None]:
        orch = self._make_orchestrator(deep)
        session = AgentSession(service=service)
        return orch.stream_analyze(service, since, session)

    async def get_fix(self, incident_id: str) -> dict[str, Any]:
        # Local mode: fix state is in-memory only; not persisted across runs
        raise NotImplementedError(
            "Fix retrieval by ID requires server mode — the incident must have been analyzed "
            "in the same process. Use `tinker analyze` first, then `tinker fix` immediately."
        )

    # ── Ops ───────────────────────────────────────────────────────────────────

    async def health(self) -> dict[str, Any]:
        from tinker import __version__
        return {
            "status": "ok",
            "mode": "local",
            "backend": self._cfg.backend,
            "version": __version__,
        }

    # ── Local-only helpers ────────────────────────────────────────────────────

    def backend(self):
        """Return the raw backend — for commands that need it directly (monitor)."""
        return self._backend

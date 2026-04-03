"""MCP server for Grafana Stack (Loki + Prometheus + Tempo).

Registration in .claude/settings.json:
{
  "mcpServers": {
    "tinker-grafana": {
      "command": "tinker-grafana-mcp",
      "env": {
        "GRAFANA_LOKI_URL": "http://loki:3100",
        "GRAFANA_PROMETHEUS_URL": "http://prometheus:9090",
        "GRAFANA_TEMPO_URL": "http://tempo:3200"
      }
    }
  }
}
For Grafana Cloud add GRAFANA_API_KEY.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from mcp.types import Tool

from tinker.agent.guardrails import sanitize_log_content
from tinker.backends.grafana import GrafanaBackend
from tinker.mcp_servers.base import TinkerMCPServer

log = structlog.get_logger(__name__)


class GrafanaMCPServer(TinkerMCPServer):
    server_name = "tinker-grafana"
    server_description = "Query Loki logs (LogQL), Prometheus metrics (PromQL), and Tempo traces"

    def __init__(self) -> None:
        super().__init__(backend=GrafanaBackend())

    def _register_tools(self) -> None:

        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            return [
                Tool(
                    name="loki_query_logs",
                    description=(
                        "Query Loki using LogQL. Pass a label selector {app='foo'} or a plain "
                        "string for keyword search. Returns log lines sorted newest first."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "service": {"type": "string"},
                            "query": {"type": "string", "description": "LogQL expression or keyword"},
                            "since": {"type": "string", "default": "1h"},
                            "limit": {"type": "integer", "default": 100},
                        },
                        "required": ["service", "query"],
                    },
                ),
                Tool(
                    name="prometheus_get_metrics",
                    description="Query Prometheus with PromQL. Returns a time series.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "service": {"type": "string"},
                            "metric_name": {"type": "string", "description": "Metric name or full PromQL expression"},
                            "since": {"type": "string", "default": "1h"},
                        },
                        "required": ["service", "metric_name"],
                    },
                ),
                Tool(
                    name="tempo_search_traces",
                    description="Search Tempo for recent traces for a service.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "service": {"type": "string"},
                            "limit": {"type": "integer", "default": 20},
                        },
                        "required": ["service"],
                    },
                ),
                Tool(
                    name="grafana_detect_anomalies",
                    description="Detect error log spikes (Loki) and HTTP 5xx rate (Prometheus).",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "service": {"type": "string"},
                            "window_minutes": {"type": "integer", "default": 10},
                        },
                        "required": ["service"],
                    },
                ),
            ]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict[str, Any]):
            log.info("mcp.tool_call", server=self.server_name, tool=name)
            try:
                match name:
                    case "loki_query_logs":
                        return await self._handle_query_logs(arguments)
                    case "prometheus_get_metrics":
                        return await self._handle_get_metrics(arguments)
                    case "tempo_search_traces":
                        return await self._handle_search_traces(arguments)
                    case "grafana_detect_anomalies":
                        return await self._handle_detect_anomalies(arguments)
                    case _:
                        return self._error(f"Unknown tool: {name}")
            except Exception as exc:
                log.exception("mcp.tool_error", tool=name)
                return self._error(str(exc))

    async def _handle_query_logs(self, args: dict[str, Any]):
        end = datetime.now(timezone.utc)
        start = self._backend._parse_since(args.get("since", "1h"))
        entries = await self._backend.query_logs(
            args["service"], args["query"], start, end, args.get("limit", 100)
        )
        return self._text([
            {"timestamp": e.timestamp.isoformat(), "level": e.level,
             "message": sanitize_log_content(e.message)}
            for e in entries
        ])

    async def _handle_get_metrics(self, args: dict[str, Any]):
        end = datetime.now(timezone.utc)
        start = self._backend._parse_since(args.get("since", "1h"))
        points = await self._backend.get_metrics(
            args["service"], args["metric_name"], start, end
        )
        return self._text([{"timestamp": p.timestamp.isoformat(), "value": p.value} for p in points])

    async def _handle_search_traces(self, args: dict[str, Any]):
        # GrafanaBackend has search_traces as a bonus method
        from tinker.backends.grafana import GrafanaBackend
        assert isinstance(self._backend, GrafanaBackend)
        traces = await self._backend.search_traces(args["service"], limit=args.get("limit", 20))
        return self._text(traces)

    async def _handle_detect_anomalies(self, args: dict[str, Any]):
        anomalies = await self._backend.detect_anomalies(
            args["service"], args.get("window_minutes", 10)
        )
        return self._text([a.to_dict() for a in anomalies])


def main() -> None:
    GrafanaMCPServer().main()


if __name__ == "__main__":
    main()

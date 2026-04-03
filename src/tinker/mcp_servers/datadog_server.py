"""MCP server for Datadog (Logs + Metrics + APM).

Registration in .claude/settings.json:
{
  "mcpServers": {
    "tinker-datadog": {
      "command": "tinker-datadog-mcp",
      "env": {
        "DATADOG_API_KEY": "${DATADOG_API_KEY}",
        "DATADOG_APP_KEY": "${DATADOG_APP_KEY}",
        "DATADOG_SITE": "datadoghq.com"
      }
    }
  }
}
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from mcp.types import Tool

from tinker.agent.guardrails import sanitize_log_content
from tinker.backends.datadog import DatadogBackend
from tinker.mcp_servers.base import TinkerMCPServer

log = structlog.get_logger(__name__)


class DatadogMCPServer(TinkerMCPServer):
    server_name = "tinker-datadog"
    server_description = "Query Datadog Logs, Metrics, and APM traces"

    def __init__(self) -> None:
        super().__init__(backend=DatadogBackend())

    def _register_tools(self) -> None:

        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            return [
                Tool(
                    name="datadog_query_logs",
                    description=(
                        "Search Datadog logs. Uses Datadog log query syntax: "
                        "service:payments status:error @http.status_code:500"
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "service": {"type": "string"},
                            "query": {"type": "string", "description": "Datadog log query"},
                            "since": {"type": "string", "default": "1h"},
                            "limit": {"type": "integer", "default": 100},
                        },
                        "required": ["service", "query"],
                    },
                ),
                Tool(
                    name="datadog_get_metrics",
                    description=(
                        "Fetch a Datadog metric timeseries. "
                        "Supports full Datadog metric query syntax: avg:aws.lambda.errors{service:foo}"
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "service": {"type": "string"},
                            "metric_name": {"type": "string"},
                            "since": {"type": "string", "default": "1h"},
                        },
                        "required": ["service", "metric_name"],
                    },
                ),
                Tool(
                    name="datadog_search_traces",
                    description="Search Datadog APM for error traces in a service.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "service": {"type": "string"},
                            "query": {"type": "string", "default": "status:error"},
                            "limit": {"type": "integer", "default": 20},
                        },
                        "required": ["service"],
                    },
                ),
                Tool(
                    name="datadog_detect_anomalies",
                    description="Detect log error spikes and metric anomalies in Datadog.",
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
                    case "datadog_query_logs":
                        return await self._handle_query_logs(arguments)
                    case "datadog_get_metrics":
                        return await self._handle_get_metrics(arguments)
                    case "datadog_search_traces":
                        return await self._handle_search_traces(arguments)
                    case "datadog_detect_anomalies":
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
             "message": sanitize_log_content(e.message), "trace_id": e.trace_id}
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
        from tinker.backends.datadog import DatadogBackend
        assert isinstance(self._backend, DatadogBackend)
        traces = await self._backend.search_traces(
            args["service"], args.get("query", "status:error"), args.get("limit", 20)
        )
        return self._text(traces)

    async def _handle_detect_anomalies(self, args: dict[str, Any]):
        anomalies = await self._backend.detect_anomalies(
            args["service"], args.get("window_minutes", 10)
        )
        return self._text([a.to_dict() for a in anomalies])


def main() -> None:
    DatadogMCPServer().main()


if __name__ == "__main__":
    main()

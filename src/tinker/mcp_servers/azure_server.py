"""MCP server for Azure Monitor Logs + Metrics.

Registration in .claude/settings.json:
{
  "mcpServers": {
    "tinker-azure": {
      "command": "tinker-azure-mcp",
      "env": {
        "AZURE_LOG_ANALYTICS_WORKSPACE_ID": "...",
        "AZURE_SUBSCRIPTION_ID": "...",
        "AZURE_RESOURCE_GROUP": "..."
      }
    }
  }
}
Credentials are picked up automatically via DefaultAzureCredential
(Managed Identity on Azure, az login locally).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from mcp.types import Tool

from tinker.agent.guardrails import sanitize_log_content
from tinker.backends.azure import AzureBackend
from tinker.mcp_servers.base import TinkerMCPServer

log = structlog.get_logger(__name__)


class AzureMCPServer(TinkerMCPServer):
    server_name = "tinker-azure"
    server_description = "Query Azure Monitor Logs (KQL) and Azure Monitor Metrics"

    def __init__(self) -> None:
        super().__init__(backend=AzureBackend())

    def _register_tools(self) -> None:

        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            return [
                Tool(
                    name="azure_query_logs",
                    description=(
                        "Run a KQL query against Azure Log Analytics. "
                        "Pass a plain string for keyword search or full KQL for advanced queries."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "service": {"type": "string", "description": "AppRoleName / service name"},
                            "query": {"type": "string", "description": "KQL query or keyword"},
                            "since": {"type": "string", "default": "1h"},
                            "limit": {"type": "integer", "default": 100},
                        },
                        "required": ["service", "query"],
                    },
                ),
                Tool(
                    name="azure_get_metrics",
                    description="Fetch Azure Monitor Metrics for a resource.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "service": {"type": "string", "description": "App name or full resource URI"},
                            "metric_name": {"type": "string", "description": "e.g. Requests, Http5xx, CpuPercentage"},
                            "since": {"type": "string", "default": "1h"},
                        },
                        "required": ["service", "metric_name"],
                    },
                ),
                Tool(
                    name="azure_detect_anomalies",
                    description="Detect exception spikes in Application Insights for a service.",
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
                    case "azure_query_logs":
                        return await self._handle_query_logs(arguments)
                    case "azure_get_metrics":
                        return await self._handle_get_metrics(arguments)
                    case "azure_detect_anomalies":
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

    async def _handle_detect_anomalies(self, args: dict[str, Any]):
        anomalies = await self._backend.detect_anomalies(
            args["service"], args.get("window_minutes", 10)
        )
        return self._text([a.to_dict() for a in anomalies])


def main() -> None:
    AzureMCPServer().main()


if __name__ == "__main__":
    main()

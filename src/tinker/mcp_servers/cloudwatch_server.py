"""MCP server for AWS CloudWatch Logs and Metrics.

Registration in .claude/settings.json:
{
  "mcpServers": {
    "tinker-cloudwatch": {
      "command": "tinker-cloudwatch-mcp",
      "env": {
        "AWS_PROFILE": "prod-readonly",
        "AWS_REGION": "us-east-1"
      }
    }
  }
}
"""

from __future__ import annotations

from datetime import timezone, datetime
from typing import Any

import structlog
from mcp.types import Tool

from tinker.agent.guardrails import sanitize_log_content
from tinker.backends.cloudwatch import CloudWatchBackend
from tinker.mcp_servers.base import TinkerMCPServer

log = structlog.get_logger(__name__)


class CloudWatchMCPServer(TinkerMCPServer):
    server_name = "tinker-cloudwatch"
    server_description = "Query AWS CloudWatch Logs and Metrics for incident analysis"

    def __init__(self) -> None:
        super().__init__(backend=CloudWatchBackend())

    def _register_tools(self) -> None:

        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            return [
                Tool(
                    name="cloudwatch_query_logs",
                    description=(
                        "Run a CloudWatch Logs Insights query for a service. "
                        "Returns matching log entries sorted by timestamp descending."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "service": {"type": "string", "description": "Lambda function or log group prefix"},
                            "query": {"type": "string", "description": "CloudWatch Logs Insights query string"},
                            "since": {"type": "string", "description": "Time window: 1h, 30m, 2d", "default": "1h"},
                            "limit": {"type": "integer", "default": 100},
                        },
                        "required": ["service", "query"],
                    },
                ),
                Tool(
                    name="cloudwatch_get_metrics",
                    description="Fetch a CloudWatch metric time series for a service.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "service": {"type": "string"},
                            "metric_name": {"type": "string", "description": "CloudWatch metric name e.g. Errors, Duration, Throttles"},
                            "since": {"type": "string", "default": "1h"},
                            "namespace": {"type": "string", "default": "AWS/Lambda"},
                        },
                        "required": ["service", "metric_name"],
                    },
                ),
                Tool(
                    name="cloudwatch_detect_anomalies",
                    description="Automatically detect error spikes and metric anomalies for a service.",
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
                    case "cloudwatch_query_logs":
                        return await self._handle_query_logs(arguments)
                    case "cloudwatch_get_metrics":
                        return await self._handle_get_metrics(arguments)
                    case "cloudwatch_detect_anomalies":
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
            service=args["service"],
            query=args["query"],
            start=start,
            end=end,
            limit=args.get("limit", 100),
        )
        result = [
            {
                "timestamp": e.timestamp.isoformat(),
                "level": e.level,
                "message": sanitize_log_content(e.message),
                "trace_id": e.trace_id,
            }
            for e in entries
        ]
        return self._text(result)

    async def _handle_get_metrics(self, args: dict[str, Any]):
        end = datetime.now(timezone.utc)
        start = self._backend._parse_since(args.get("since", "1h"))
        points = await self._backend.get_metrics(
            service=args["service"],
            metric_name=args["metric_name"],
            start=start,
            end=end,
        )
        return self._text([{"timestamp": p.timestamp.isoformat(), "value": p.value} for p in points])

    async def _handle_detect_anomalies(self, args: dict[str, Any]):
        anomalies = await self._backend.detect_anomalies(
            service=args["service"],
            window_minutes=args.get("window_minutes", 10),
        )
        return self._text([a.to_dict() for a in anomalies])


def main() -> None:
    CloudWatchMCPServer().main()


if __name__ == "__main__":
    main()

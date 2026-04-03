"""MCP server for Elasticsearch / OpenSearch.

Registration in .claude/settings.json:
{
  "mcpServers": {
    "tinker-elastic": {
      "command": "tinker-elastic-mcp",
      "env": {
        "ELASTICSEARCH_URL": "https://my-cluster.es.io:9243",
        "ELASTICSEARCH_API_KEY": "..."
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
from tinker.backends.elastic import ElasticBackend
from tinker.mcp_servers.base import TinkerMCPServer

log = structlog.get_logger(__name__)


class ElasticMCPServer(TinkerMCPServer):
    server_name = "tinker-elastic"
    server_description = "Query Elasticsearch / OpenSearch logs and metrics for incident analysis"

    def __init__(self) -> None:
        super().__init__(backend=ElasticBackend())

    def _register_tools(self) -> None:

        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            return [
                Tool(
                    name="elastic_query_logs",
                    description=(
                        "Query Elasticsearch logs using Lucene/KQL syntax. "
                        "Searches the logs-* index pattern by default."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "service": {"type": "string", "description": "service.name field value"},
                            "query": {"type": "string", "description": "Lucene or KQL query string"},
                            "since": {"type": "string", "default": "1h"},
                            "limit": {"type": "integer", "default": 100},
                        },
                        "required": ["service", "query"],
                    },
                ),
                Tool(
                    name="elastic_get_metrics",
                    description="Aggregate a numeric field over time using date_histogram.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "service": {"type": "string"},
                            "metric_field": {"type": "string", "description": "Numeric field to aggregate e.g. http.response.duration"},
                            "since": {"type": "string", "default": "1h"},
                        },
                        "required": ["service", "metric_field"],
                    },
                ),
                Tool(
                    name="elastic_detect_anomalies",
                    description="Detect high error rates for a service in a recent time window.",
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
                    case "elastic_query_logs":
                        return await self._handle_query_logs(arguments)
                    case "elastic_get_metrics":
                        return await self._handle_get_metrics(arguments)
                    case "elastic_detect_anomalies":
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
        return self._text([
            {
                "timestamp": e.timestamp.isoformat(),
                "level": e.level,
                "message": sanitize_log_content(e.message),
                "trace_id": e.trace_id,
            }
            for e in entries
        ])

    async def _handle_get_metrics(self, args: dict[str, Any]):
        end = datetime.now(timezone.utc)
        start = self._backend._parse_since(args.get("since", "1h"))
        points = await self._backend.get_metrics(
            service=args["service"],
            metric_name=args["metric_field"],
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
    ElasticMCPServer().main()


if __name__ == "__main__":
    main()

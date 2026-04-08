"""MCP over SSE endpoint — allows Claude Code to connect to Tinker as a remote MCP server.

Claude Code configuration (.claude/settings.json):
{
  "mcpServers": {
    "tinker": {
      "transport": "sse",
      "url": "https://tinker.your-company.internal/mcp/sse",
      "headers": {
        "Authorization": "Bearer ${TINKR_API_TOKEN}"
      }
    }
  }
}

This single endpoint exposes ALL tools from all configured backends,
plus the GitHub/codebase tools. The active backend is determined by
the active profile in config.toml — clients don't need to know
which cloud provider is in use.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request, Response
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import TextContent, Tool

from tinker.backends import get_backend
from tinker.backends.base import ObservabilityBackend
from tinker.backends.sanitize import sanitize_log_content

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/mcp", tags=["mcp"])

# ── Build the unified MCP server ──────────────────────────────────────────────

_mcp_server = Server("tinker")
_transport = SseServerTransport("/mcp/messages")


def _text(content: object) -> list[TextContent]:
    import json
    if isinstance(content, str):
        text = content
    else:
        try:
            text = json.dumps(content, default=str, indent=2)
        except Exception:
            text = str(content)
    return [TextContent(type="text", text=text)]


@_mcp_server.list_tools()
async def list_tools() -> list[Tool]:
    """Return all Tinker tools. The observability tools reflect the active backend."""
    from tinker import toml_config as tc
    cfg = tc.get()
    profile = cfg.active_profile_config()
    backend_name = profile.backend if profile else "unknown"
    return [
        # ── Observability tools (backend-agnostic names) ──────────────────
        Tool(
            name="query_logs",
            description=(
                f"Query logs for a service. Active backend: {backend_name}. "
                "Returns recent log entries matching the query."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "query": {"type": "string", "description": "Search query / filter expression"},
                    "since": {"type": "string", "default": "1h"},
                    "limit": {"type": "integer", "default": 100},
                },
                "required": ["service", "query"],
            },
        ),
        Tool(
            name="get_recent_errors",
            description="Return recent ERROR-level log entries for a service.",
            inputSchema={
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "minutes": {"type": "integer", "default": 30},
                },
                "required": ["service"],
            },
        ),
        Tool(
            name="get_metrics",
            description=f"Fetch a metric time series for a service. Active backend: {backend_name}.",
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
            name="detect_anomalies",
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
        # ── Codebase tools ────────────────────────────────────────────────
        Tool(
            name="get_file",
            description="Read a source file from the monitored repository.",
            inputSchema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        ),
        Tool(
            name="search_code",
            description="Search the codebase with ripgrep.",
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "file_glob": {"type": "string", "default": "**/*.py"},
                    "context_lines": {"type": "integer", "default": 3},
                },
                "required": ["pattern"],
            },
        ),
        Tool(
            name="suggest_fix",
            description="Stage a fix as a unified diff. Does NOT apply it — requires /approve.",
            inputSchema={
                "type": "object",
                "properties": {
                    "incident_id": {"type": "string"},
                    "diff": {"type": "string"},
                    "explanation": {"type": "string"},
                },
                "required": ["incident_id", "diff", "explanation"],
            },
        ),
    ]


@_mcp_server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    from datetime import timezone, datetime

    log.info("mcp_sse.tool_call", tool=name)
    try:
        backend: ObservabilityBackend = get_backend()

        match name:
            case "query_logs":
                end = datetime.now(timezone.utc)
                start = backend._parse_since(arguments.get("since", "1h"))
                entries = await backend.query_logs(
                    arguments["service"], arguments["query"], start, end,
                    arguments.get("limit", 100),
                )
                return _text([
                    {"timestamp": e.timestamp.isoformat(), "level": e.level,
                     "message": sanitize_log_content(e.message), "trace_id": e.trace_id}
                    for e in entries
                ])

            case "get_recent_errors":
                entries = await backend.get_recent_errors(
                    arguments["service"], arguments.get("minutes", 30)
                )
                return _text([
                    {"timestamp": e.timestamp.isoformat(),
                     "message": sanitize_log_content(e.message)}
                    for e in entries
                ])

            case "get_metrics":
                end = datetime.now(timezone.utc)
                start = backend._parse_since(arguments.get("since", "1h"))
                points = await backend.get_metrics(
                    arguments["service"], arguments["metric_name"], start, end
                )
                return _text([{"timestamp": p.timestamp.isoformat(), "value": p.value} for p in points])

            case "detect_anomalies":
                anomalies = await backend.detect_anomalies(
                    arguments["service"], arguments.get("window_minutes", 10)
                )
                return _text([a.to_dict() for a in anomalies])

            case "get_file":
                from tinker.code.repo import RepoClient
                content = RepoClient(".").read_file(arguments["path"])
                return _text(content)

            case "search_code":
                from tinker.code.repo import RepoClient
                result = RepoClient(".").search(
                    arguments["pattern"],
                    arguments.get("file_glob", "**/*.py"),
                    arguments.get("context_lines", 3),
                )
                return _text(result)

            case "suggest_fix":
                return _text({
                    "status": "staged",
                    "incident_id": arguments["incident_id"],
                    "message": (
                        f"Fix staged for {arguments['incident_id']}. "
                        "POST /api/v1/approve to apply."
                    ),
                })

            case _:
                return _text(f"ERROR: Unknown tool '{name}'")

    except Exception as exc:
        log.exception("mcp_sse.tool_error", tool=name)
        return _text(f"ERROR: {exc}")


# ── FastAPI routes ────────────────────────────────────────────────────────────


@router.get("/sse")
async def mcp_sse(request: Request) -> Response:
    """SSE endpoint — Claude Code connects here for remote MCP."""
    async with _transport.connect_sse(
        request.scope, request.receive, request._send  # type: ignore[attr-defined]
    ) as streams:
        await _mcp_server.run(
            streams[0],
            streams[1],
            _mcp_server.create_initialization_options(),
        )
    return Response()


@router.post("/messages")
async def mcp_messages(request: Request) -> Response:
    """POST endpoint for MCP message exchange (required alongside SSE)."""
    await _transport.handle_post_message(
        request.scope, request.receive, request._send  # type: ignore[attr-defined]
    )
    return Response()

"""Base class for all Tinker MCP servers.

Each concrete server wraps an ObservabilityBackend and exposes its capabilities
as MCP tools. The harness (Claude Code / any MCP client) discovers and calls
these tools over stdio or SSE transport.

Pattern for creating a new MCP server
--------------------------------------
1. Subclass TinkerMCPServer
2. Override `server_name` and `server_description`
3. Call `self._register_tools()` in __init__ after calling super().__init__()
4. Implement the `_register_tools` method — use @self.server.list_tools() and
   @self.server.call_tool() decorators
5. Add an entry-point in pyproject.toml:
       [project.scripts]
       tinker-<name>-mcp = "tinker.mcp_servers.<name>_server:main"
6. Register in .claude/settings.json under mcpServers
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

import structlog
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from tinker.backends.base import ObservabilityBackend

log = structlog.get_logger(__name__)


class TinkerMCPServer(ABC):
    """Wraps an ObservabilityBackend as an MCP server."""

    #: Override in subclasses
    server_name: str = "tinker-base"
    server_description: str = "Tinker observability MCP server"

    def __init__(self, backend: ObservabilityBackend) -> None:
        self._backend = backend
        self.server = Server(self.server_name)
        self._register_tools()
        log.info("mcp_server.init", name=self.server_name)

    @abstractmethod
    def _register_tools(self) -> None:
        """Register @server.list_tools() and @server.call_tool() handlers."""
        ...

    # ── Shared helpers ────────────────────────────────────────────────────────

    def _text(self, content: Any) -> list[TextContent]:
        """Wrap any value as an MCP TextContent response."""
        import json

        if isinstance(content, str):
            text = content
        else:
            try:
                text = json.dumps(content, default=str, indent=2)
            except Exception:
                text = str(content)
        return [TextContent(type="text", text=text)]

    def _error(self, message: str) -> list[TextContent]:
        return [TextContent(type="text", text=f"ERROR: {message}")]

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def run_stdio(self) -> None:
        """Start the server on stdio transport (default for Claude Code MCP)."""
        log.info("mcp_server.start_stdio", name=self.server_name)
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options(),
            )

    def main(self) -> None:
        """Entry point called by the pyproject.toml script."""
        asyncio.run(self.run_stdio())

"""MCP server wrappers — each observability backend exposed as an MCP server.

Each server can be registered in .claude/settings.json under `mcpServers`
so that Claude Code, the CLI, and the Slack bot all consume the same tool
definitions over the MCP protocol.
"""

from tinker.mcp_servers.base import TinkerMCPServer

__all__ = ["TinkerMCPServer"]

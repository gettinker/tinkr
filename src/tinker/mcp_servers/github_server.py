"""MCP server for GitHub — codebase access and PR creation.

Registration in .claude/settings.json:
{
  "mcpServers": {
    "tinker-github": {
      "command": "tinker-github-mcp",
      "env": {
        "GITHUB_TOKEN": "ghp_...",
        "GITHUB_REPO": "your-org/your-repo",
        "TINKR_REPO_PATH": "/path/to/local/clone"
      }
    }
  }
}
"""

from __future__ import annotations

from typing import Any

import structlog
from mcp.types import Tool

from tinker.mcp_servers.base import TinkerMCPServer
from tinker.backends.base import ObservabilityBackend

log = structlog.get_logger(__name__)


class _NoOpBackend(ObservabilityBackend):
    """Placeholder — GitHub server doesn't wrap an observability backend."""

    async def query_logs(self, *a, **kw):  # type: ignore[override]
        return []

    async def get_metrics(self, *a, **kw):  # type: ignore[override]
        return []

    async def detect_anomalies(self, *a, **kw):  # type: ignore[override]
        return []


class GitHubMCPServer(TinkerMCPServer):
    server_name = "tinker-github"
    server_description = "Read source files, search code, and create PRs on GitHub"

    def __init__(self) -> None:
        from tinker.config import settings
        super().__init__(backend=_NoOpBackend())
        self._repo_path = settings.tinker_repo_path or "."

    def _register_tools(self) -> None:

        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            return [
                Tool(
                    name="github_get_file",
                    description="Read a source file from the monitored repository.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "File path relative to repo root"},
                        },
                        "required": ["path"],
                    },
                ),
                Tool(
                    name="github_search_code",
                    description="Search the codebase with ripgrep. Returns matching lines with context.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string", "description": "Regex or literal pattern"},
                            "file_glob": {"type": "string", "default": "**/*.py"},
                            "context_lines": {"type": "integer", "default": 3},
                        },
                        "required": ["pattern"],
                    },
                ),
                Tool(
                    name="github_recent_commits",
                    description="List recent commits touching a file path or directory.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "default": "."},
                            "n": {"type": "integer", "default": 10},
                        },
                    },
                ),
                Tool(
                    name="github_blame",
                    description="Get git blame for a specific line in a file.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string"},
                            "line_number": {"type": "integer"},
                        },
                        "required": ["file_path", "line_number"],
                    },
                ),
                Tool(
                    name="github_create_pr",
                    description=(
                        "Apply a unified diff and open a GitHub PR. "
                        "REQUIRES prior human approval — call suggest_fix first and get approval."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "diff": {"type": "string", "description": "Unified diff (--- a/ +++ b/)"},
                            "branch_name": {"type": "string"},
                            "title": {"type": "string"},
                            "body": {"type": "string"},
                        },
                        "required": ["diff", "branch_name", "title", "body"],
                    },
                ),
            ]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict[str, Any]):
            log.info("mcp.tool_call", server=self.server_name, tool=name)
            try:
                match name:
                    case "github_get_file":
                        return self._handle_get_file(arguments)
                    case "github_search_code":
                        return self._handle_search_code(arguments)
                    case "github_recent_commits":
                        return self._handle_recent_commits(arguments)
                    case "github_blame":
                        return self._handle_blame(arguments)
                    case "github_create_pr":
                        return await self._handle_create_pr(arguments)
                    case _:
                        return self._error(f"Unknown tool: {name}")
            except Exception as exc:
                log.exception("mcp.tool_error", tool=name)
                return self._error(str(exc))

    def _handle_get_file(self, args: dict[str, Any]):
        from tinker.code.repo import RepoClient
        content = RepoClient(self._repo_path).read_file(args["path"])
        return self._text(content)

    def _handle_search_code(self, args: dict[str, Any]):
        from tinker.code.repo import RepoClient
        result = RepoClient(self._repo_path).search(
            pattern=args["pattern"],
            glob=args.get("file_glob", "**/*.py"),
            context_lines=args.get("context_lines", 3),
        )
        return self._text(result)

    def _handle_recent_commits(self, args: dict[str, Any]):
        from tinker.code.repo import RepoClient
        commits = RepoClient(self._repo_path).recent_commits(
            service_path=args.get("path", "."),
            n=args.get("n", 10),
        )
        return self._text(commits)

    def _handle_blame(self, args: dict[str, Any]):
        from tinker.code.repo import RepoClient
        result = RepoClient(self._repo_path).blame(args["file_path"], args["line_number"])
        return self._text(result)

    async def _handle_create_pr(self, args: dict[str, Any]):
        from tinker.code.fix_applier import FixApplier
        applier = FixApplier(self._repo_path)
        pr_url = await applier.create_pr(
            diff=args["diff"],
            branch_name=args["branch_name"],
            title=args["title"],
            body=args["body"],
        )
        return self._text({"pr_url": pr_url})


def main() -> None:
    GitHubMCPServer().main()


if __name__ == "__main__":
    main()

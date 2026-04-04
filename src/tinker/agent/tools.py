"""Agent tool definitions (OpenAI function-call format) and dispatcher.

LiteLLM translates these to the native format for each provider:
  - Anthropic  → input_schema / tool_use blocks
  - OpenAI     → function calling
  - OpenRouter → passes through to the underlying provider

Tool schemas use the OpenAI `{"type": "function", "function": {...}}` envelope.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from tinker.agent.guardrails import GuardRailChain, sanitize_log_content
from tinker.backends import get_backend
from tinker.backends.base import ObservabilityBackend

log = structlog.get_logger(__name__)


# ── Tool schema definitions (OpenAI function-call format) ─────────────────────

def _fn(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """Shorthand for building an OpenAI function tool definition."""
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    _fn(
        "query_logs",
        (
            "Query logs from the configured observability backend for a service. "
            "Returns up to `limit` log entries matching the query in the given time range."
        ),
        {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Service or application name"},
                "query": {"type": "string", "description": "Provider-specific query string"},
                "since": {
                    "type": "string",
                    "description": "How far back to search, e.g. '1h', '30m', '2d'",
                    "default": "1h",
                },
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["service", "query"],
        },
    ),
    _fn(
        "get_recent_errors",
        "Return recent ERROR-level log entries for a service.",
        {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "minutes": {"type": "integer", "default": 30},
            },
            "required": ["service"],
        },
    ),
    _fn(
        "get_metrics",
        "Fetch a metric time series for a service.",
        {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "metric_name": {"type": "string"},
                "since": {"type": "string", "default": "1h"},
            },
            "required": ["service", "metric_name"],
        },
    ),
    _fn(
        "detect_anomalies",
        "Automatically detect error spikes and metric anomalies for a service.",
        {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "window_minutes": {"type": "integer", "default": 10},
            },
            "required": ["service"],
        },
    ),
    _fn(
        "get_file",
        "Read a source file from the monitored repository.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to repo root"},
            },
            "required": ["path"],
        },
    ),
    _fn(
        "search_code",
        "Search the codebase for a pattern using ripgrep.",
        {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "file_glob": {"type": "string", "default": "**/*.py"},
                "context_lines": {"type": "integer", "default": 3},
            },
            "required": ["pattern"],
        },
    ),
    _fn(
        "get_recent_commits",
        "List recent git commits touching a file path or directory.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": "."},
                "n": {"type": "integer", "default": 10},
            },
        },
    ),
    _fn(
        "suggest_fix",
        (
            "Propose a fix as a unified diff. The diff is stored and NOT applied until "
            "the user runs /tinker-approve or --approve."
        ),
        {
            "type": "object",
            "properties": {
                "incident_id": {"type": "string"},
                "diff": {"type": "string", "description": "Unified diff (--- a/ +++ b/ format)"},
                "explanation": {"type": "string"},
            },
            "required": ["incident_id", "diff", "explanation"],
        },
    ),
    _fn(
        "glob_files",
        "Find files in the repository matching a glob pattern.",
        {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern relative to repo root, e.g. 'src/**/*.py'",
                },
                "max_results": {"type": "integer", "default": 30},
            },
            "required": ["pattern"],
        },
    ),
    _fn(
        "apply_fix",
        (
            "Apply a previously suggested fix and open a PR. "
            "REQUIRES explicit human approval — will fail without it."
        ),
        {
            "type": "object",
            "properties": {
                "incident_id": {"type": "string"},
                "branch_name": {"type": "string"},
            },
            "required": ["incident_id"],
        },
    ),
]


# ── Dispatcher ────────────────────────────────────────────────────────────────

class ToolDispatcher:
    """Validates guardrails then routes tool calls to their implementations."""

    def __init__(
        self,
        guardrails: GuardRailChain | None = None,
        backend: ObservabilityBackend | None = None,
        repo_path: str | None = None,
    ) -> None:
        self._guardrails = guardrails or GuardRailChain()
        self._backend = backend
        self._repo_path = repo_path
        self._pending_fixes: dict[str, dict[str, str]] = {}

    def _get_backend(self) -> ObservabilityBackend:
        if self._backend:
            return self._backend
        return get_backend()

    async def dispatch(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        context: dict[str, Any],
    ) -> Any:
        self._guardrails.check(tool_name, tool_input, context)

        match tool_name:
            case "query_logs":
                return await self._query_logs(tool_input)
            case "get_recent_errors":
                return await self._get_recent_errors(tool_input)
            case "get_metrics":
                return await self._get_metrics(tool_input)
            case "detect_anomalies":
                return await self._detect_anomalies(tool_input)
            case "get_file":
                return self._get_file(tool_input)
            case "glob_files":
                return self._glob_files(tool_input)
            case "search_code":
                return self._search_code(tool_input)
            case "get_recent_commits":
                return self._get_recent_commits(tool_input)
            case "suggest_fix":
                return self._suggest_fix(tool_input)
            case "apply_fix":
                return await self._apply_fix(tool_input, context)
            case _:
                raise ValueError(f"Unknown tool: {tool_name}")

    # ── Tool implementations ──────────────────────────────────────────────────

    async def _query_logs(self, inp: dict[str, Any]) -> list[dict[str, Any]]:
        backend = self._get_backend()
        end = datetime.now(timezone.utc)
        start = backend._parse_since(inp.get("since", "1h"))
        entries = await backend.query_logs(
            service=inp["service"],
            query=inp["query"],
            start=start,
            end=end,
            limit=inp.get("limit", 100),
        )
        return [
            {
                "timestamp": e.timestamp.isoformat(),
                "level": e.level,
                "message": sanitize_log_content(e.message),
                "service": e.service,
                "trace_id": e.trace_id,
            }
            for e in entries
        ]

    async def _get_recent_errors(self, inp: dict[str, Any]) -> list[dict[str, Any]]:
        backend = self._get_backend()
        entries = await backend.get_recent_errors(
            service=inp["service"],
            minutes=inp.get("minutes", 30),
        )
        return [
            {
                "timestamp": e.timestamp.isoformat(),
                "message": sanitize_log_content(e.message),
                "trace_id": e.trace_id,
            }
            for e in entries
        ]

    async def _get_metrics(self, inp: dict[str, Any]) -> list[dict[str, Any]]:
        backend = self._get_backend()
        end = datetime.now(timezone.utc)
        start = backend._parse_since(inp.get("since", "1h"))
        points = await backend.get_metrics(
            service=inp["service"],
            metric_name=inp["metric_name"],
            start=start,
            end=end,
        )
        return [{"timestamp": p.timestamp.isoformat(), "value": p.value} for p in points]

    async def _detect_anomalies(self, inp: dict[str, Any]) -> list[dict[str, Any]]:
        backend = self._get_backend()
        anomalies = await backend.detect_anomalies(
            service=inp["service"],
            window_minutes=inp.get("window_minutes", 10),
        )
        return [a.to_dict() for a in anomalies]

    def _get_file(self, inp: dict[str, Any]) -> str:
        import os
        repo = self._repo_path or "."
        path = os.path.join(repo, inp["path"].lstrip("/"))
        try:
            with open(path) as f:
                return f.read()
        except FileNotFoundError:
            return f"ERROR: File not found: {inp['path']}"

    def _glob_files(self, inp: dict[str, Any]) -> str:
        import glob as glob_mod
        import os
        repo = self._repo_path or "."
        pattern = os.path.join(repo, inp["pattern"].lstrip("/"))
        max_r = inp.get("max_results", 30)
        BINARY_EXTS = {
            ".pyc", ".so", ".o", ".a", ".dylib", ".dll", ".exe",
            ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".tar", ".gz",
        }
        matches = [
            m[len(os.path.abspath(repo)) + 1:]
            for m in glob_mod.glob(pattern, recursive=True)
            if os.path.isfile(m) and os.path.splitext(m)[1].lower() not in BINARY_EXTS
        ]
        return "\n".join(matches[:max_r]) or "(no matches)"

    def _search_code(self, inp: dict[str, Any]) -> str:
        import subprocess
        repo = self._repo_path or "."
        result = subprocess.run(
            [
                "rg",
                "--glob", inp.get("file_glob", "**/*.py"),
                "--context", str(inp.get("context_lines", 3)),
                inp["pattern"],
                repo,
            ],
            capture_output=True,
            text=True,
        )
        return result.stdout or "(no matches)"

    def _get_recent_commits(self, inp: dict[str, Any]) -> list[dict[str, str]]:
        from tinker.code.repo import RepoClient
        return RepoClient(self._repo_path or ".").recent_commits(
            service_path=inp.get("path", "."),
            n=inp.get("n", 10),
        )

    def _suggest_fix(self, inp: dict[str, Any]) -> dict[str, str]:
        incident_id = inp["incident_id"]
        self._pending_fixes[incident_id] = {
            "diff": inp["diff"],
            "explanation": inp["explanation"],
        }
        log.info("fix.suggested", incident_id=incident_id)
        return {
            "status": "pending_approval",
            "incident_id": incident_id,
            "message": (
                f"Fix staged for incident {incident_id}. "
                "Run /tinker-approve or --approve to apply."
            ),
        }

    async def _apply_fix(self, inp: dict[str, Any], context: dict[str, Any]) -> dict[str, str]:
        from tinker.code.fix_applier import FixApplier

        incident_id = inp["incident_id"]
        pending = self._pending_fixes.get(incident_id)
        if not pending:
            return {"status": "error", "message": f"No pending fix for incident {incident_id}"}

        applier = FixApplier(repo_path=self._repo_path or ".")
        pr_url = await applier.create_pr(
            diff=pending["diff"],
            branch_name=inp.get("branch_name", f"tinker/fix-{incident_id}"),
            title=f"fix: tinker auto-fix for {incident_id}",
            body=pending["explanation"],
        )
        del self._pending_fixes[incident_id]
        return {"status": "success", "pr_url": pr_url}

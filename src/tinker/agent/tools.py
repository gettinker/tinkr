"""Agent tool definitions and dispatcher."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from tinker.agent.guardrails import GuardRailChain, sanitize_log_content
from tinker.backends import get_backend
from tinker.backends.base import ObservabilityBackend

log = structlog.get_logger(__name__)

# ── Tool schema definitions (sent to Claude) ──────────────────────────────────

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "query_logs",
        "description": (
            "Query logs from the configured observability backend for a service. "
            "Returns up to `limit` log entries matching the query in the given time range."
        ),
        "input_schema": {
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
                "backend": {
                    "type": "string",
                    "enum": ["cloudwatch", "elastic", "gcp"],
                    "default": "cloudwatch",
                },
            },
            "required": ["service", "query"],
        },
    },
    {
        "name": "get_recent_errors",
        "description": "Return recent ERROR-level log entries for a service.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "minutes": {"type": "integer", "default": 30},
                "backend": {"type": "string", "default": "cloudwatch"},
            },
            "required": ["service"],
        },
    },
    {
        "name": "get_metrics",
        "description": "Fetch a metric time series for a service.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "metric_name": {"type": "string"},
                "since": {"type": "string", "default": "1h"},
                "backend": {"type": "string", "default": "cloudwatch"},
            },
            "required": ["service", "metric_name"],
        },
    },
    {
        "name": "get_file",
        "description": "Read a source file from the monitored repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to repo root"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_code",
        "description": "Search the codebase for a pattern using ripgrep.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "file_glob": {"type": "string", "default": "**/*.py"},
                "context_lines": {"type": "integer", "default": 3},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "suggest_fix",
        "description": (
            "Propose a fix as a unified diff. The diff is stored and NOT applied until "
            "the user runs /tinker-approve or --approve."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "incident_id": {"type": "string"},
                "diff": {"type": "string", "description": "Unified diff (--- a/ +++ b/ format)"},
                "explanation": {"type": "string"},
            },
            "required": ["incident_id", "diff", "explanation"],
        },
    },
    {
        "name": "apply_fix",
        "description": (
            "Apply a previously suggested fix and open a PR. "
            "REQUIRES explicit human approval — will fail without it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "incident_id": {"type": "string"},
                "branch_name": {"type": "string"},
            },
            "required": ["incident_id"],
        },
    },
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
        # pending diffs keyed by incident_id
        self._pending_fixes: dict[str, dict[str, str]] = {}

    def _get_backend(self, name: str) -> ObservabilityBackend:
        if self._backend:
            return self._backend
        return get_backend(name)

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
            case "get_file":
                return self._get_file(tool_input)
            case "search_code":
                return self._search_code(tool_input)
            case "suggest_fix":
                return self._suggest_fix(tool_input)
            case "apply_fix":
                return await self._apply_fix(tool_input, context)
            case _:
                raise ValueError(f"Unknown tool: {tool_name}")

    # ── Tool implementations ──────────────────────────────────────────────────

    async def _query_logs(self, inp: dict[str, Any]) -> list[dict[str, Any]]:
        backend = self._get_backend(inp.get("backend", "cloudwatch"))
        since = inp.get("since", "1h")
        end = datetime.now(timezone.utc)
        start = backend._parse_since(since)
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
        backend = self._get_backend(inp.get("backend", "cloudwatch"))
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
        backend = self._get_backend(inp.get("backend", "cloudwatch"))
        since = inp.get("since", "1h")
        end = datetime.now(timezone.utc)
        start = backend._parse_since(since)
        points = await backend.get_metrics(
            service=inp["service"],
            metric_name=inp["metric_name"],
            start=start,
            end=end,
        )
        return [{"timestamp": p.timestamp.isoformat(), "value": p.value} for p in points]

    def _get_file(self, inp: dict[str, Any]) -> str:
        import os

        repo = self._repo_path or "."
        path = os.path.join(repo, inp["path"].lstrip("/"))
        try:
            with open(path) as f:
                return f.read()
        except FileNotFoundError:
            return f"ERROR: File not found: {inp['path']}"

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

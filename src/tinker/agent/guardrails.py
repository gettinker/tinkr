"""Guardrails: approval gates, RBAC, audit logging, and input sanitization."""

from __future__ import annotations

import re
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Tools that require explicit human approval before execution
APPROVAL_REQUIRED_TOOLS: frozenset[str] = frozenset(
    {"apply_fix", "create_pr", "restart_service", "rollback_deploy"}
)

# Slack role → allowed tool names
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "dev": {"query_logs", "get_metrics", "get_recent_errors", "get_file", "search_code"},
    "sre": {
        "query_logs", "get_metrics", "get_recent_errors", "get_file",
        "search_code", "suggest_fix", "search_traces",
    },
    "oncall": {
        "query_logs", "get_metrics", "get_recent_errors", "get_file",
        "search_code", "suggest_fix", "search_traces", "apply_fix", "create_pr",
    },
    "sre-lead": {"*"},  # all tools
}

# Patterns that look like prompt injection or credential leakage
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+previous\s+instructions", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+", re.IGNORECASE),
    re.compile(r"system\s+prompt", re.IGNORECASE),
    re.compile(r"AKIA[A-Z0-9]{16}"),          # AWS access key
    re.compile(r"sk-ant-[A-Za-z0-9\-]+"),     # Anthropic API key
    re.compile(r"xox[bpa]-[A-Za-z0-9\-]+"),   # Slack tokens
    re.compile(r"ghp_[A-Za-z0-9]{36}"),       # GitHub tokens
]


class PendingApprovalError(Exception):
    """Raised when a tool requires approval that hasn't been granted yet."""

    def __init__(self, tool: str, session_id: str) -> None:
        self.tool = tool
        self.session_id = session_id
        super().__init__(
            f"Tool '{tool}' requires human approval. "
            f"Use /tinker-approve or --approve to confirm (session: {session_id})."
        )


class PermissionDeniedError(Exception):
    """Raised when a user's role doesn't allow the requested tool."""


# ── Base class ────────────────────────────────────────────────────────────────


class GuardRail(ABC):
    """Base class for all guardrails. Each check method raises on violation."""

    @abstractmethod
    def check(self, tool_name: str, tool_input: dict[str, Any], context: dict[str, Any]) -> None:
        """Raise an appropriate exception if the guardrail is violated."""
        ...


# ── Concrete guardrails ───────────────────────────────────────────────────────


class ApprovalRequired(GuardRail):
    """Blocks execution of destructive tools until human approval is recorded."""

    def check(self, tool_name: str, tool_input: dict[str, Any], context: dict[str, Any]) -> None:
        if tool_name not in APPROVAL_REQUIRED_TOOLS:
            return
        approved = context.get("approved_tools", set())
        session_id = context.get("session_id", "unknown")
        if tool_name not in approved:
            raise PendingApprovalError(tool_name, session_id)


class RBACGuard(GuardRail):
    """Checks that the actor's roles permit the tool they are requesting."""

    def check(self, tool_name: str, tool_input: dict[str, Any], context: dict[str, Any]) -> None:
        actor_roles: list[str] = context.get("actor_roles", [])
        if not actor_roles:
            return  # CLI / local — no role enforcement

        for role in actor_roles:
            allowed = ROLE_PERMISSIONS.get(role, set())
            if "*" in allowed or tool_name in allowed:
                return

        raise PermissionDeniedError(
            f"None of the actor's roles {actor_roles} permit tool '{tool_name}'."
        )


class AuditLogger(GuardRail):
    """Logs every tool call. Does not block — always passes."""

    def check(self, tool_name: str, tool_input: dict[str, Any], context: dict[str, Any]) -> None:
        # Strip secrets from tool_input before logging
        safe_input = {k: v for k, v in tool_input.items() if "token" not in k and "key" not in k}
        log.info(
            "agent.tool_call",
            tool=tool_name,
            session_id=context.get("session_id"),
            actor=context.get("actor"),
            approved_by=context.get("approved_by"),
            timestamp=datetime.now(timezone.utc).isoformat(),
            input=safe_input,
        )


# ── Input sanitization ────────────────────────────────────────────────────────


def sanitize_log_content(content: str) -> str:
    """Remove patterns that look like credentials or prompt injections.

    Call this on any external data (log lines, metrics labels) before
    including it in a prompt sent to the LLM.
    """
    for pattern in _INJECTION_PATTERNS:
        content = pattern.sub("[REDACTED]", content)
    return content


# ── Composite gate ────────────────────────────────────────────────────────────


class GuardRailChain:
    """Runs multiple guardrails in order. Stops at the first violation."""

    def __init__(self, rails: list[GuardRail] | None = None) -> None:
        self._rails: list[GuardRail] = rails or [
            AuditLogger(),
            RBACGuard(),
            ApprovalRequired(),
        ]

    def check(self, tool_name: str, tool_input: dict[str, Any], context: dict[str, Any]) -> None:
        for rail in self._rails:
            rail.check(tool_name, tool_input, context)

    def grant_approval(self, context: dict[str, Any], tool_name: str, approved_by: str) -> None:
        """Record that a human has approved a specific tool call for this session."""
        context.setdefault("approved_tools", set()).add(tool_name)
        context["approved_by"] = approved_by
        log.info(
            "agent.approval_granted",
            tool=tool_name,
            approved_by=approved_by,
            session_id=context.get("session_id"),
        )

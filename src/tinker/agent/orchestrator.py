"""Core agent orchestration loop using Claude tool-use."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import anthropic
import structlog

from tinker.agent.guardrails import GuardRailChain, PendingApprovalError
from tinker.agent.prompts import RCA_SYSTEM_PROMPT
from tinker.agent.tools import TOOL_DEFINITIONS, ToolDispatcher
from tinker.config import settings

log = structlog.get_logger(__name__)


# ── Data models ───────────────────────────────────────────────────────────────


@dataclass
class IncidentReport:
    incident_id: str
    service: str
    severity: str  # critical | high | medium | low
    root_cause: str
    summary: str
    affected_services: list[str]
    suggested_fix: str | None = None
    fix_diff: str | None = None
    timeline: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "service": self.service,
            "severity": self.severity,
            "root_cause": self.root_cause,
            "summary": self.summary,
            "affected_services": self.affected_services,
            "suggested_fix": self.suggested_fix,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class AgentSession:
    """Holds all state for one analysis conversation."""

    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    service: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    incident_report: IncidentReport | None = None

    def __post_init__(self) -> None:
        self.context["session_id"] = self.session_id

    def grant_approval(self, tool_name: str, approved_by: str, guardrails: GuardRailChain) -> None:
        guardrails.grant_approval(self.context, tool_name, approved_by)


# ── Orchestrator ──────────────────────────────────────────────────────────────


class Orchestrator:
    """Runs the Claude agentic loop: prompt → tool calls → results → repeat."""

    MAX_ITERATIONS = 20  # guard against infinite loops

    def __init__(
        self,
        dispatcher: ToolDispatcher | None = None,
        guardrails: GuardRailChain | None = None,
        use_deep_rca: bool = False,
    ) -> None:
        self._client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key.get_secret_value()
        )
        self._guardrails = guardrails or GuardRailChain()
        self._dispatcher = dispatcher or ToolDispatcher(guardrails=self._guardrails)
        self._model = settings.deep_rca_model if use_deep_rca else settings.default_model
        self._use_deep_rca = use_deep_rca

    async def analyze(
        self,
        service: str,
        since: str = "1h",
        session: AgentSession | None = None,
    ) -> IncidentReport:
        """Run a full RCA analysis for a service. Returns a structured IncidentReport."""
        if session is None:
            session = AgentSession(service=service)

        prompt = (
            f"Analyze the '{service}' service for incidents in the last {since}. "
            "Query the logs for errors, check relevant metrics, look at the source code "
            "for anything related to the errors you find, and produce a full incident report "
            "with root cause, severity, and a suggested fix."
        )

        session.messages.append({"role": "user", "content": prompt})
        await self._run_loop(session)

        # Build report from final assistant message
        last_text = self._extract_last_text(session.messages)
        report = IncidentReport(
            incident_id=f"INC-{session.session_id}",
            service=service,
            severity="unknown",
            root_cause=last_text,
            summary=last_text[:200],
            affected_services=[service],
        )
        session.incident_report = report
        return report

    async def chat(
        self,
        user_message: str,
        session: AgentSession,
    ) -> str:
        """Send a follow-up message in an existing session."""
        session.messages.append({"role": "user", "content": user_message})
        await self._run_loop(session)
        return self._extract_last_text(session.messages)

    async def stream_analyze(
        self,
        service: str,
        since: str = "1h",
        session: AgentSession | None = None,
    ) -> AsyncIterator[str]:
        """Stream analysis progress text as the agent works."""
        if session is None:
            session = AgentSession(service=service)

        prompt = (
            f"Analyze the '{service}' service for incidents in the last {since}. "
            "Think step by step, explain your reasoning as you query logs and code."
        )
        session.messages.append({"role": "user", "content": prompt})

        async for chunk in self._run_loop_streaming(session):
            yield chunk

    # ── Internal loop ─────────────────────────────────────────────────────────

    async def _run_loop(self, session: AgentSession) -> None:
        for _ in range(self.MAX_ITERATIONS):
            kwargs: dict[str, Any] = {
                "model": self._model,
                "max_tokens": 8192,
                "system": RCA_SYSTEM_PROMPT,
                "tools": TOOL_DEFINITIONS,
                "messages": session.messages,
            }
            if self._use_deep_rca:
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": 8000}

            response = self._client.messages.create(**kwargs)
            session.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                break

            if response.stop_reason == "tool_use":
                tool_results = await self._process_tool_calls(response.content, session)
                session.messages.append({"role": "user", "content": tool_results})
            else:
                log.warning("unexpected_stop_reason", reason=response.stop_reason)
                break

    async def _run_loop_streaming(self, session: AgentSession) -> AsyncIterator[str]:
        for _ in range(self.MAX_ITERATIONS):
            full_content: list[Any] = []
            stop_reason: str | None = None

            with self._client.messages.stream(
                model=self._model,
                max_tokens=8192,
                system=RCA_SYSTEM_PROMPT,
                tools=TOOL_DEFINITIONS,
                messages=session.messages,
            ) as stream:
                for event in stream:
                    if hasattr(event, "type"):
                        if event.type == "content_block_delta":
                            delta = getattr(event, "delta", None)
                            if delta and hasattr(delta, "text"):
                                yield delta.text
                response = stream.get_final_message()
                full_content = list(response.content)
                stop_reason = response.stop_reason

            session.messages.append({"role": "assistant", "content": full_content})

            if stop_reason == "end_turn":
                break

            if stop_reason == "tool_use":
                tool_results = await self._process_tool_calls(full_content, session)
                session.messages.append({"role": "user", "content": tool_results})

    async def _process_tool_calls(
        self,
        content_blocks: list[Any],
        session: AgentSession,
    ) -> list[dict[str, Any]]:
        results = []
        for block in content_blocks:
            if block.type != "tool_use":
                continue
            tool_name: str = block.name
            tool_input: dict[str, Any] = block.input

            log.info("agent.tool_use", tool=tool_name, session=session.session_id)
            try:
                result = await self._dispatcher.dispatch(tool_name, tool_input, session.context)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(result),
                })
            except PendingApprovalError as exc:
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "is_error": True,
                    "content": str(exc),
                })
            except Exception as exc:
                log.exception("agent.tool_error", tool=tool_name)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "is_error": True,
                    "content": f"Tool error: {exc}",
                })
        return results

    @staticmethod
    def _extract_last_text(messages: list[dict[str, Any]]) -> str:
        for msg in reversed(messages):
            if msg["role"] == "assistant":
                content = msg["content"]
                if isinstance(content, str):
                    return content
                for block in content:
                    if hasattr(block, "type") and block.type == "text":
                        return block.text
        return ""

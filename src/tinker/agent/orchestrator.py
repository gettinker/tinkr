"""Core agent orchestration loop using LiteLLM (provider-agnostic).

Supports any model string that LiteLLM understands:
  anthropic/claude-sonnet-4-6
  openrouter/anthropic/claude-opus-4-6
  openrouter/openai/gpt-4o
  groq/llama-3.1-70b-versatile
  ollama/llama3

Set TINKER_DEFAULT_MODEL and TINKER_DEEP_RCA_MODEL in your environment.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import structlog

from tinker.agent import llm
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
    severity: str          # critical | high | medium | low
    root_cause: str
    summary: str
    affected_services: list[str]
    suggested_fix: str | None = None
    fix_diff: str | None = None
    timeline: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    model_used: str = ""
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
            "model_used": self.model_used,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class AgentSession:
    """Holds all state for one analysis conversation."""

    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    service: str = ""
    # OpenAI-format message history
    messages: list[dict[str, Any]] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    incident_report: IncidentReport | None = None

    def __post_init__(self) -> None:
        self.context["session_id"] = self.session_id

    def grant_approval(self, tool_name: str, approved_by: str, guardrails: GuardRailChain) -> None:
        guardrails.grant_approval(self.context, tool_name, approved_by)


# ── Orchestrator ──────────────────────────────────────────────────────────────

class Orchestrator:
    """Provider-agnostic agent loop via LiteLLM.

    The model is selected by config. All providers are supported as long as
    they implement function/tool calling. Streaming and non-streaming paths
    both work.
    """

    MAX_ITERATIONS = 20

    def __init__(
        self,
        dispatcher: ToolDispatcher | None = None,
        guardrails: GuardRailChain | None = None,
        use_deep_rca: bool = False,
        model: str | None = None,
    ) -> None:
        self._guardrails = guardrails or GuardRailChain()
        self._dispatcher = dispatcher or ToolDispatcher(guardrails=self._guardrails)
        self._model = model or (
            settings.deep_rca_model if use_deep_rca else settings.default_model
        )
        self._use_deep_rca = use_deep_rca
        log.info("orchestrator.init", model=self._model)

    async def analyze(
        self,
        service: str,
        since: str = "1h",
        session: AgentSession | None = None,
    ) -> IncidentReport:
        """Run a full RCA analysis. Returns a structured IncidentReport."""
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

        last_text = self._last_text(session.messages)
        report = IncidentReport(
            incident_id=f"INC-{session.session_id}",
            service=service,
            severity="unknown",
            root_cause=last_text,
            summary=last_text[:200],
            affected_services=[service],
            model_used=self._model,
        )
        session.incident_report = report
        return report

    async def chat(self, user_message: str, session: AgentSession) -> str:
        """Send a follow-up message in an existing session."""
        session.messages.append({"role": "user", "content": user_message})
        await self._run_loop(session)
        return self._last_text(session.messages)

    async def stream_analyze(
        self,
        service: str,
        since: str = "1h",
        session: AgentSession | None = None,
    ) -> AsyncIterator[str]:
        """Stream analysis tokens as the agent works."""
        if session is None:
            session = AgentSession(service=service)

        prompt = (
            f"Analyze the '{service}' service for incidents in the last {since}. "
            "Think step by step, explain your reasoning as you query logs and code."
        )
        session.messages.append({"role": "user", "content": prompt})
        async for chunk in self._run_loop_streaming(session):
            yield chunk

    # ── Internal loop (non-streaming) ─────────────────────────────────────────

    async def _run_loop(self, session: AgentSession) -> None:
        system = [{"role": "system", "content": RCA_SYSTEM_PROMPT}]

        for _ in range(self.MAX_ITERATIONS):
            response = llm.complete(
                messages=system + session.messages,
                model=self._model,
                tools=TOOL_DEFINITIONS,
                thinking=self._use_deep_rca,
            )

            assistant_msg = llm.assistant_message_from_response(response)
            session.messages.append(assistant_msg)

            if not llm.is_tool_call(response):
                break

            tool_results = await self._process_tool_calls(response, session)
            session.messages.extend(tool_results)

    # ── Internal loop (streaming) ─────────────────────────────────────────────

    async def _run_loop_streaming(self, session: AgentSession) -> AsyncIterator[str]:
        system = [{"role": "system", "content": RCA_SYSTEM_PROMPT}]

        for _ in range(self.MAX_ITERATIONS):
            # Collect streamed text and detect tool calls at the end
            collected_text = ""
            async for chunk in llm.stream_complete(
                messages=system + session.messages,
                model=self._model,
                tools=TOOL_DEFINITIONS,
            ):
                collected_text += chunk
                yield chunk

            # After streaming, do a non-streaming call to get structured tool calls
            # (most providers don't stream tool call arguments reliably)
            if collected_text:
                session.messages.append({"role": "assistant", "content": collected_text})

            # Check if we need to continue with tool calls
            check_response = llm.complete(
                messages=system + session.messages,
                model=self._model,
                tools=TOOL_DEFINITIONS,
            )
            if not llm.is_tool_call(check_response):
                break

            assistant_msg = llm.assistant_message_from_response(check_response)
            session.messages.append(assistant_msg)
            tool_results = await self._process_tool_calls(check_response, session)
            session.messages.extend(tool_results)

    # ── Tool call processing ──────────────────────────────────────────────────

    async def _process_tool_calls(
        self,
        response: Any,
        session: AgentSession,
    ) -> list[dict[str, Any]]:
        tool_calls = llm.extract_tool_calls(response)
        results = []

        for tc in tool_calls:
            tool_name: str = tc["name"]
            tool_input: dict[str, Any] = tc["arguments"]
            call_id: str = tc["id"]

            log.info("agent.tool_use", tool=tool_name, session=session.session_id)
            try:
                result = await self._dispatcher.dispatch(tool_name, tool_input, session.context)
                results.append(llm.tool_result_message(call_id, result))
            except PendingApprovalError as exc:
                results.append(llm.tool_result_message(call_id, f"BLOCKED: {exc}"))
            except Exception as exc:
                log.exception("agent.tool_error", tool=tool_name)
                results.append(llm.tool_result_message(call_id, f"ERROR: {exc}"))

        return results

    @staticmethod
    def _last_text(messages: list[dict[str, Any]]) -> str:
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                content = msg.get("content")
                if isinstance(content, str):
                    return content
        return ""

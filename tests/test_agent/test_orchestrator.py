"""Tests for the agent orchestrator (Claude calls mocked)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinker.agent.orchestrator import AgentSession, Orchestrator


class TestAgentSession:
    def test_session_id_generated(self):
        session = AgentSession()
        assert len(session.session_id) > 0

    def test_context_has_session_id(self):
        session = AgentSession()
        assert session.context["session_id"] == session.session_id

    def test_grant_approval(self):
        from tinker.agent.guardrails import GuardRailChain
        session = AgentSession()
        chain = GuardRailChain()
        session.grant_approval("apply_fix", "bob", chain)
        assert "apply_fix" in session.context.get("approved_tools", set())


class TestOrchestrator:
    def _make_mock_response(self, text: str, stop_reason: str = "end_turn"):
        block = MagicMock()
        block.type = "text"
        block.text = text
        response = MagicMock()
        response.content = [block]
        response.stop_reason = stop_reason
        return response

    @patch("tinker.agent.orchestrator.settings")
    @patch("tinker.agent.orchestrator.anthropic.Anthropic")
    @pytest.mark.asyncio
    async def test_analyze_returns_report(self, mock_anthropic_cls, mock_settings):
        mock_settings.anthropic_api_key.get_secret_value.return_value = "test-key"
        mock_settings.default_model = "claude-sonnet-4-6"
        mock_settings.deep_rca_model = "claude-opus-4-6"

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_mock_response(
            "Root cause: NullPointerException in PaymentHandler.process() at line 42."
        )

        orch = Orchestrator()
        report = await orch.analyze("payments-api", since="1h")

        assert report.service == "payments-api"
        assert "payments-api" in report.incident_id
        assert "NullPointerException" in report.root_cause

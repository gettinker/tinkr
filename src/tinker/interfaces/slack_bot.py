"""Tinker Slack bot — Bolt for Python with Socket Mode."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from tinker.agent.guardrails import ROLE_PERMISSIONS, PermissionDeniedError
from tinker.agent.orchestrator import AgentSession, Orchestrator
from tinker.backends.base import Anomaly
from tinker.config import settings

log = structlog.get_logger(__name__)

# ── Session store (in-memory; use Redis in production) ────────────────────────

_sessions: dict[str, AgentSession] = {}  # keyed by Slack thread_ts
_SESSION_TTL = timedelta(hours=4)
_session_created_at: dict[str, datetime] = {}


def _get_session(thread_ts: str, service: str = "") -> AgentSession:
    now = datetime.now(timezone.utc)
    # Expire old sessions
    expired = [
        k for k, t in _session_created_at.items()
        if now - t > _SESSION_TTL
    ]
    for k in expired:
        _sessions.pop(k, None)
        _session_created_at.pop(k, None)

    if thread_ts not in _sessions:
        _sessions[thread_ts] = AgentSession(service=service)
        _session_created_at[thread_ts] = now
    return _sessions[thread_ts]


# ── RBAC ──────────────────────────────────────────────────────────────────────

# Map Slack user group handles → tinker roles (configure per workspace)
_GROUP_TO_ROLE: dict[str, str] = {
    "sre-team": "sre",
    "oncall": "oncall",
    "sre-leads": "sre-lead",
    "engineers": "dev",
}


def _get_user_roles(client: Any, user_id: str) -> list[str]:
    """Fetch Slack user groups and map to Tinker roles."""
    try:
        resp = client.users_info(user=user_id)
        # In a real impl, call usergroups.users.list for each group
        # and check membership. Simplified here:
        return ["dev"]  # default
    except Exception:
        log.exception("slack.rbac_fetch_failed", user=user_id)
        return []


def _check_permission(roles: list[str], tool_or_command: str) -> None:
    for role in roles:
        allowed = ROLE_PERMISSIONS.get(role, set())
        if "*" in allowed or tool_or_command in allowed:
            return
    raise PermissionDeniedError(
        f"Your role ({roles}) does not have permission to use `{tool_or_command}`."
    )


# ── App setup ─────────────────────────────────────────────────────────────────


def build_app() -> AsyncApp:
    token = settings.slack_bot_token
    secret = settings.slack_signing_secret
    if not token or not secret:
        raise RuntimeError("SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET must be set")

    app = AsyncApp(
        token=token.get_secret_value(),
        signing_secret=secret.get_secret_value(),
    )

    # ── Slash commands ────────────────────────────────────────────────────────

    @app.command("/tinker-analyze")
    async def handle_analyze(ack: Any, body: dict[str, Any], say: Any, client: Any) -> None:
        await ack()
        text: str = body.get("text", "").strip()
        user_id: str = body["user_id"]
        channel: str = body["channel_id"]

        if not text:
            await say("Usage: `/tinker-analyze <service-name> [since=1h]`")
            return

        parts = text.split()
        service = parts[0]
        since = "1h"
        for part in parts[1:]:
            if part.startswith("since="):
                since = part[6:]

        # Post initial message to get a thread_ts
        posted = await client.chat_postMessage(
            channel=channel,
            text=f":mag: Analyzing `{service}` (last {since})...",
        )
        thread_ts: str = posted["ts"]
        session = _get_session(thread_ts, service)
        session.context["actor"] = user_id
        session.context["actor_roles"] = _get_user_roles(client, user_id)

        async def run_analysis() -> None:
            orch = Orchestrator()
            try:
                report = await orch.analyze(service, since, session)
                blocks = _format_incident_blocks(report)
                await client.chat_update(
                    channel=channel,
                    ts=thread_ts,
                    text=f"Incident report for `{service}`",
                    blocks=blocks,
                )
            except Exception as exc:
                log.exception("slack.analyze_failed", service=service)
                await client.chat_update(
                    channel=channel,
                    ts=thread_ts,
                    text=f":x: Analysis failed: {exc}",
                )

        asyncio.create_task(run_analysis())

    @app.command("/tinker-fix")
    async def handle_fix(ack: Any, body: dict[str, Any], say: Any, client: Any) -> None:
        await ack()
        incident_id = body.get("text", "").strip()
        user_id = body["user_id"]

        if not incident_id:
            await say("Usage: `/tinker-fix <incident-id>`")
            return

        await say(
            f":wrench: Preparing fix for `{incident_id}`...\n"
            f"Reply with `/tinker-approve {incident_id}` to apply it."
        )

    @app.command("/tinker-approve")
    async def handle_approve(ack: Any, body: dict[str, Any], say: Any, client: Any) -> None:
        await ack()
        incident_id = body.get("text", "").strip()
        user_id = body["user_id"]
        channel = body["channel_id"]

        roles = _get_user_roles(client, user_id)
        try:
            _check_permission(roles, "apply_fix")
        except PermissionDeniedError as exc:
            await say(f":no_entry: {exc}")
            return

        await say(f":white_check_mark: <@{user_id}> approved fix for `{incident_id}`. Applying...")
        log.info("slack.fix_approved", incident_id=incident_id, approved_by=user_id)
        # TODO: look up session by incident_id and call apply_fix

    @app.command("/tinker-status")
    async def handle_status(ack: Any, body: dict[str, Any], say: Any) -> None:
        await ack()
        active = len(_sessions)
        await say(f":bar_chart: Tinker has {active} active session(s).")

    @app.command("/tinker-help")
    async def handle_help(ack: Any, say: Any) -> None:
        await ack()
        await say(
            "*Tinker — AI Incident Response Agent*\n\n"
            "• `/tinker-analyze <service> [since=1h]` — analyze a service for incidents\n"
            "• `/tinker-fix <incident-id>` — get the suggested fix for an incident\n"
            "• `/tinker-approve <incident-id>` — apply fix and open a PR _(requires oncall role)_\n"
            "• `/tinker-status` — show active sessions\n"
            "• `/tinker-help` — this message\n"
        )

    return app


# ── Formatting helpers ────────────────────────────────────────────────────────

SEVERITY_EMOJI = {
    "critical": ":rotating_light:",
    "high": ":red_circle:",
    "medium": ":large_yellow_circle:",
    "low": ":large_green_circle:",
    "unknown": ":white_circle:",
}


def _format_incident_blocks(report: Any) -> list[dict[str, Any]]:
    emoji = SEVERITY_EMOJI.get(report.severity, ":white_circle:")
    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{emoji} Incident {report.incident_id} — {report.service}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Severity:*\n{report.severity.upper()}"},
                {"type": "mrkdwn", "text": f"*Service:*\n{report.service}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Root Cause:*\n{report.root_cause[:800]}"},
        },
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": ":wrench: Get Fix"},
                    "value": f"fix:{report.incident_id}",
                    "action_id": "get_fix",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": ":white_check_mark: Approve"},
                    "value": f"approve:{report.incident_id}",
                    "action_id": "approve_fix",
                    "style": "primary",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": ":x: Dismiss"},
                    "value": f"dismiss:{report.incident_id}",
                    "action_id": "dismiss",
                    "style": "danger",
                },
            ],
        },
    ]


# ── Monitoring alert formatter ────────────────────────────────────────────────


async def post_anomaly_alert(app: AsyncApp, channel: str, anomaly: Anomaly) -> None:
    """Called by MonitoringLoop to post a proactive alert."""
    emoji = SEVERITY_EMOJI.get(anomaly.severity, ":white_circle:")
    await app.client.chat_postMessage(
        channel=channel,
        text=f"{emoji} Anomaly detected in `{anomaly.service}`",
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{emoji} *Anomaly: {anomaly.service}*\n"
                        f"*Metric:* `{anomaly.metric}`\n"
                        f"*Severity:* {anomaly.severity.upper()}\n"
                        f"*Details:* {anomaly.description}\n\n"
                        f"Use `/tinker-analyze {anomaly.service}` to investigate."
                    ),
                },
            }
        ],
    )


# ── Entry point ───────────────────────────────────────────────────────────────


async def start_bot() -> None:
    app = build_app()
    app_token = settings.slack_app_token
    if not app_token:
        raise RuntimeError("SLACK_APP_TOKEN must be set for Socket Mode")
    handler = AsyncSocketModeHandler(app, app_token.get_secret_value())
    log.info("slack_bot.starting")
    await handler.start_async()

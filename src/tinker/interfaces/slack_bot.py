"""Tinker Slack bot — Bolt for Python with Socket Mode.

Design
------
Slash commands are thin adapters:
  1. Parse the Slack payload
  2. Get a RemoteClient (same as CLI)
  3. Call the shared handler from interfaces/handlers.py
  4. Format the result as Slack blocks and post it

The business logic (data fetching, filtering) lives in handlers.py.
Only the Slack presentation layer (blocks, formatting) lives here.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from tinker.agent.guardrails import ROLE_PERMISSIONS, PermissionDeniedError

log = structlog.get_logger(__name__)

SEVERITY_EMOJI = {
    "critical": ":rotating_light:",
    "high": ":red_circle:",
    "medium": ":large_yellow_circle:",
    "low": ":large_green_circle:",
    "unknown": ":white_circle:",
}


# ── Client helper ─────────────────────────────────────────────────────────────

def _get_client():
    from tinker.client import get_client
    return get_client()


# ── RBAC ──────────────────────────────────────────────────────────────────────

def _get_user_roles(client: Any, user_id: str) -> list[str]:
    try:
        # In production: call usergroups.users.list and check membership
        return ["dev"]
    except Exception:
        log.exception("slack.rbac_fetch_failed", user=user_id)
        return []


def _check_permission(roles: list[str], command: str) -> None:
    for role in roles:
        allowed = ROLE_PERMISSIONS.get(role, set())
        if "*" in allowed or command in allowed:
            return
    raise PermissionDeniedError(
        f"Your role ({roles}) does not have permission to use `{command}`."
    )


# ── Session store ─────────────────────────────────────────────────────────────

from tinker.agent.orchestrator import AgentSession

_sessions: dict[str, AgentSession] = {}
_SESSION_TTL = timedelta(hours=4)
_session_created_at: dict[str, datetime] = {}


def _get_session(thread_ts: str, service: str = "") -> AgentSession:
    now = datetime.now(timezone.utc)
    expired = [k for k, t in _session_created_at.items() if now - t > _SESSION_TTL]
    for k in expired:
        _sessions.pop(k, None)
        _session_created_at.pop(k, None)
    if thread_ts not in _sessions:
        _sessions[thread_ts] = AgentSession(service=service)
        _session_created_at[thread_ts] = now
    return _sessions[thread_ts]


# ── App setup ─────────────────────────────────────────────────────────────────

def build_app() -> AsyncApp:
    from tinker.config import settings
    token = settings.slack_bot_token
    secret = settings.slack_signing_secret
    if not token or not secret:
        raise RuntimeError("SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET must be set")

    app = AsyncApp(
        token=token.get_secret_value(),
        signing_secret=secret.get_secret_value(),
    )

    # ── /tinker-logs ──────────────────────────────────────────────────────────
    @app.command("/tinker-logs")
    async def handle_logs(ack: Any, body: dict[str, Any], say: Any, client: Any) -> None:
        """Usage: /tinker-logs <service> [since=30m] [q=level:ERROR]"""
        await ack()
        text = body.get("text", "").strip()
        if not text:
            await say("Usage: `/tinker-logs <service> [since=30m] [q=<query>]`")
            return

        parts = text.split()
        service = parts[0]
        since = "30m"
        query = "*"
        for part in parts[1:]:
            if part.startswith("since="):
                since = part[6:]
            elif part.startswith("q="):
                query = part[2:]

        async def run():
            from tinker.interfaces.handlers import get_logs
            try:
                entries = await get_logs(_get_client(), service, query, since, limit=10)
                if not entries:
                    await say(f"No log entries found for `{service}` (last {since}).")
                    return
                lines = [f"*Logs* — `{service}` (last {since})", ""]
                for e in entries:
                    ts = e.timestamp.strftime("%H:%M:%S")
                    lines.append(f"`{ts}` *{e.level}* {e.message[:120]}")
                await say("\n".join(lines))
            except Exception as exc:
                await say(f":x: Error fetching logs: {exc}")

        asyncio.create_task(run())

    # ── /tinker-anomaly ───────────────────────────────────────────────────────
    @app.command("/tinker-anomaly")
    async def handle_anomaly(ack: Any, body: dict[str, Any], say: Any, client: Any) -> None:
        """Usage: /tinker-anomaly <service> [since=1h] [severity=high]"""
        await ack()
        text = body.get("text", "").strip()
        if not text:
            await say("Usage: `/tinker-anomaly <service> [since=1h] [severity=high]`")
            return

        parts = text.split()
        service = parts[0]
        since = "1h"
        severity = None
        for part in parts[1:]:
            if part.startswith("since="):
                since = part[6:]
            elif part.startswith("severity="):
                severity = part[9:]

        async def run():
            from tinker.interfaces.handlers import get_anomalies
            try:
                anomalies = await get_anomalies(_get_client(), service, since, severity)
                if not anomalies:
                    await say(f":white_check_mark: No anomalies detected for `{service}` in the last {since}.")
                    return
                lines = [f"*Anomalies* — `{service}` (last {since})", ""]
                for a in anomalies[:5]:
                    emoji = SEVERITY_EMOJI.get(a.severity.lower(), ":white_circle:")
                    lines.append(f"{emoji} *{a.severity.upper()}* `{a.metric}` — {a.description}")
                if len(anomalies) > 5:
                    lines.append(f"_…and {len(anomalies) - 5} more_")
                lines.append(f"\nUse `/tinker-analyze {service}` for a full root-cause analysis.")
                await say("\n".join(lines))
            except Exception as exc:
                await say(f":x: Error detecting anomalies: {exc}")

        asyncio.create_task(run())

    # ── /tinker-analyze ───────────────────────────────────────────────────────
    @app.command("/tinker-analyze")
    async def handle_analyze(ack: Any, body: dict[str, Any], say: Any, client: Any) -> None:
        """Usage: /tinker-analyze <service> [since=1h]"""
        await ack()
        text = body.get("text", "").strip()
        user_id = body["user_id"]
        channel = body["channel_id"]

        if not text:
            await say("Usage: `/tinker-analyze <service-name> [since=1h]`")
            return

        parts = text.split()
        service = parts[0]
        since = "1h"
        for part in parts[1:]:
            if part.startswith("since="):
                since = part[6:]

        posted = await client.chat_postMessage(
            channel=channel,
            text=f":mag: Analyzing `{service}` (last {since})...",
        )
        thread_ts = posted["ts"]
        session = _get_session(thread_ts, service)
        session.context["actor"] = user_id
        session.context["actor_roles"] = _get_user_roles(client, user_id)

        async def run_analysis():
            from tinker.agent.orchestrator import Orchestrator
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

    # ── /tinker-fix ───────────────────────────────────────────────────────────
    @app.command("/tinker-fix")
    async def handle_fix(ack: Any, body: dict[str, Any], say: Any, client: Any) -> None:
        await ack()
        incident_id = body.get("text", "").strip()
        if not incident_id:
            await say("Usage: `/tinker-fix <incident-id>`")
            return
        await say(
            f":wrench: Preparing fix for `{incident_id}`...\n"
            f"Reply with `/tinker-approve {incident_id}` to apply it."
        )

    # ── /tinker-approve ───────────────────────────────────────────────────────
    @app.command("/tinker-approve")
    async def handle_approve(ack: Any, body: dict[str, Any], say: Any, client: Any) -> None:
        await ack()
        incident_id = body.get("text", "").strip()
        user_id = body["user_id"]

        roles = _get_user_roles(client, user_id)
        try:
            _check_permission(roles, "apply_fix")
        except PermissionDeniedError as exc:
            await say(f":no_entry: {exc}")
            return

        await say(f":white_check_mark: <@{user_id}> approved fix for `{incident_id}`. Applying...")
        log.info("slack.fix_approved", incident_id=incident_id, approved_by=user_id)

    # ── /tinker-watch ─────────────────────────────────────────────────────────
    @app.command("/tinker-watch")
    async def handle_watch(ack: Any, body: dict[str, Any], say: Any, client: Any) -> None:
        """Usage: /tinker-watch start <service> [notifier=default] [interval=60]
                  /tinker-watch list
                  /tinker-watch stop <watch-id>"""
        await ack()
        text = body.get("text", "").strip()
        parts = text.split()

        if not parts:
            await say(
                "Usage:\n"
                "• `/tinker-watch start <service> [notifier=default] [interval=60]`\n"
                "• `/tinker-watch list`\n"
                "• `/tinker-watch stop <watch-id>`"
            )
            return

        subcommand = parts[0]

        if subcommand == "list":
            async def run_list():
                from tinker.interfaces.handlers import get_watches
                try:
                    watches = await get_watches(_get_client())
                    if not watches:
                        await say("No watches running on the server.")
                        return
                    lines = ["*Active Watches*", ""]
                    for w in watches:
                        status = ":green_circle:" if w.get("status") == "running" else ":white_circle:"
                        lines.append(
                            f"{status} `{w['watch_id']}` — *{w['service']}*  "
                            f"notifier={w.get('notifier') or 'default'}  "
                            f"every {w.get('interval_seconds', '?')}s"
                        )
                    await say("\n".join(lines))
                except Exception as exc:
                    await say(f":x: Error listing watches: {exc}")
            asyncio.create_task(run_list())

        elif subcommand == "start" and len(parts) >= 2:
            service = parts[1]
            notifier = None
            interval = 60
            for part in parts[2:]:
                if part.startswith("notifier="):
                    notifier = part[9:]
                elif part.startswith("interval="):
                    try:
                        interval = int(part[9:])
                    except ValueError:
                        pass

            async def run_start():
                from tinker.interfaces.handlers import start_watch
                try:
                    watch = await start_watch(_get_client(), service, notifier, None, interval)
                    await say(
                        f":eyes: Watch started: `{watch.get('watch_id', '?')}` — "
                        f"*{service}*  notifier={watch.get('notifier') or 'default'}  "
                        f"every {interval}s\n"
                        f"Stop with `/tinker-watch stop {watch.get('watch_id', '?')}`"
                    )
                except Exception as exc:
                    await say(f":x: Error starting watch: {exc}")
            asyncio.create_task(run_start())

        elif subcommand == "stop" and len(parts) >= 2:
            watch_id = parts[1]

            async def run_stop():
                from tinker.interfaces.handlers import stop_watch
                try:
                    await stop_watch(_get_client(), watch_id)
                    await say(f":white_check_mark: Watch `{watch_id}` stopped.")
                except Exception as exc:
                    await say(f":x: Error stopping watch: {exc}")
            asyncio.create_task(run_stop())

        else:
            await say(f":x: Unknown subcommand `{subcommand}`. Use `start`, `list`, or `stop`.")

    # ── /tinker-status ────────────────────────────────────────────────────────
    @app.command("/tinker-status")
    async def handle_status(ack: Any, body: dict[str, Any], say: Any) -> None:
        await ack()
        active = len(_sessions)
        await say(f":bar_chart: Tinker has {active} active session(s).")

    # ── /tinker-help ──────────────────────────────────────────────────────────
    @app.command("/tinker-help")
    async def handle_help(ack: Any, say: Any) -> None:
        await ack()
        await say(
            "*Tinker — AI Incident Response Agent*\n\n"
            "• `/tinker-logs <service> [since=30m] [q=level:ERROR]` — fetch recent logs\n"
            "• `/tinker-anomaly <service> [since=1h] [severity=high]` — detect anomalies\n"
            "• `/tinker-analyze <service> [since=1h]` — full AI root-cause analysis\n"
            "• `/tinker-fix <incident-id>` — get the suggested fix\n"
            "• `/tinker-approve <incident-id>` — apply fix and open PR _(requires oncall role)_\n"
            "• `/tinker-watch start <service> [notifier=default] [interval=60]` — start a watch\n"
            "• `/tinker-watch list` — list active watches\n"
            "• `/tinker-watch stop <watch-id>` — stop a watch\n"
            "• `/tinker-status` — show active sessions\n"
            "• `/tinker-help` — this message\n"
        )

    return app


# ── Block formatters ──────────────────────────────────────────────────────────

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


# ── Entry point ───────────────────────────────────────────────────────────────

async def start_bot() -> None:
    from tinker.config import settings
    app = build_app()
    app_token = settings.slack_app_token
    if not app_token:
        raise RuntimeError("SLACK_APP_TOKEN must be set for Socket Mode")
    handler = AsyncSocketModeHandler(app, app_token.get_secret_value())
    log.info("slack_bot.starting")
    await handler.start_async()

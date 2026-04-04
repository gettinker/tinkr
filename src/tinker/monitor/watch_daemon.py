"""Background watch daemon — runs as a detached process, posts anomalies to Slack.

Usage (internal — called by the CLI, not directly)
---------------------------------------------------
    python -m tinker.monitor.watch_daemon \\
        --watch-id watch-abc123 \\
        --service payments-api \\
        --interval 60 \\
        --channel "#incidents"

The CLI spawns this module with ``subprocess.Popen(start_new_session=True)``
so it survives terminal close.  The watch_id is written to SQLite by the CLI
before spawn; this module updates ``last_run_at`` and ``last_anomaly_hash``
on every cycle and sets status="stopped" on clean exit.

Deduplication
-------------
On each tick the set of (service, metric, severity) tuples is hashed.
If the hash matches the previous run, Slack is NOT notified again.
Anomalies are re-posted only when the set changes (new anomaly appears,
severity changes, or an anomaly clears).

Slack posting
-------------
Uses the existing ``slack_bot_token`` from config.  If no token is configured
the daemon still runs but skips Slack and logs locally.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import signal
import sys
from datetime import datetime, timezone

import structlog

log = structlog.get_logger(__name__)

_running = True


def _handle_sigterm(signum, frame):  # noqa: ANN001
    global _running
    _running = False


async def _post_slack(channel: str, text: str) -> None:
    """Post *text* to *channel* using the configured Slack bot token."""
    from tinker.config import settings
    token = settings.slack_bot_token
    if not token:
        log.info("watch.slack_not_configured", channel=channel)
        return
    try:
        import httpx
        headers = {
            "Authorization": f"Bearer {token.get_secret_value()}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                json={"channel": channel, "text": text, "mrkdwn": True},
                headers=headers,
                timeout=10,
            )
            data = resp.json()
            if not data.get("ok"):
                log.warning("watch.slack_post_failed", error=data.get("error"))
    except Exception:
        log.exception("watch.slack_post_error")


def _anomaly_hash(anomalies: list) -> str:
    """Hash the current anomaly set for deduplication."""
    sig = json.dumps(
        sorted(
            (a.service, a.metric, a.severity) for a in anomalies
        ),
        default=str,
    )
    return hashlib.sha256(sig.encode()).hexdigest()[:16]


def _format_slack_anomalies(service: str, anomalies: list) -> str:
    """Format anomaly list as a Slack message block."""
    SEVERITY_EMOJI = {
        "critical": ":red_circle:",
        "high": ":large_orange_circle:",
        "medium": ":large_yellow_circle:",
        "low": ":white_circle:",
    }
    lines = [f":bell: *Tinker Watch* — anomalies detected in *{service}*\n"]
    for i, a in enumerate(anomalies, 1):
        emoji = SEVERITY_EMOJI.get(a.severity.lower(), ":white_circle:")
        lines.append(f"{emoji} *[{a.severity.upper()}]* `{a.metric}` — {a.description}")

    lines.append(
        f"\n_Detected at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_"
    )
    lines.append("_Reply `explain <n>` in thread or run_ `tinker monitor " + service + "`")
    return "\n".join(lines)


async def _run_watch(
    watch_id: str,
    service: str,
    channel: str | None,
    interval: int,
) -> None:
    from tinker.backends import get_backend
    from tinker.store.db import TinkerDB

    backend = get_backend()
    db = TinkerDB()

    log.info(
        "watch.start",
        watch_id=watch_id,
        service=service,
        interval=interval,
        channel=channel,
    )

    last_hash: str | None = None

    # Restore previous hash from DB so we don't re-alert on restart
    record = db.get_watch(watch_id)
    if record:
        last_hash = record.get("last_anomaly_hash")

    while _running:
        try:
            anomalies = await backend.detect_anomalies(service)
            now = datetime.now(timezone.utc).isoformat()
            db.update_watch(watch_id, last_run_at=now)

            if anomalies:
                current_hash = _anomaly_hash(anomalies)
                if current_hash != last_hash:
                    last_hash = current_hash
                    db.update_watch(watch_id, last_anomaly_hash=current_hash)
                    if channel:
                        msg = _format_slack_anomalies(service, anomalies)
                        await _post_slack(channel, msg)
                    else:
                        log.info(
                            "watch.anomalies_detected",
                            service=service,
                            count=len(anomalies),
                        )
            else:
                # Anomalies cleared — reset so next occurrence fires again
                if last_hash is not None:
                    last_hash = None
                    db.update_watch(watch_id, last_anomaly_hash=None)

        except Exception:
            log.exception("watch.tick_error", service=service)

        # Sleep in 1s increments so we can respond to SIGTERM promptly
        for _ in range(interval):
            if not _running:
                break
            await asyncio.sleep(1)

    # Clean shutdown
    db.update_watch(watch_id, status="stopped")
    db.close()
    log.info("watch.stopped", watch_id=watch_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tinker background watch daemon")
    parser.add_argument("--watch-id", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--channel", default=None)
    args = parser.parse_args()

    # Redirect stdout/stderr to a log file so the detached process doesn't lose output
    import os
    log_dir = os.path.expanduser("~/.tinker/logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{args.watch_id}.log")
    sys.stdout = open(log_path, "a", buffering=1)
    sys.stderr = sys.stdout

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    asyncio.run(
        _run_watch(
            watch_id=args.watch_id,
            service=args.service,
            channel=args.channel,
            interval=args.interval,
        )
    )


if __name__ == "__main__":
    main()

"""Server-side watch manager.

Each watch is an asyncio.Task that polls detect_anomalies() on a schedule
and posts to Slack when the anomaly set changes.

The WatchManager is a singleton attached to the FastAPI app's lifespan:
  - On startup: load 'running' watches from DB and restart tasks
  - On shutdown: cancel all tasks
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from typing import Any

import structlog

from tinker.store.db import TinkerDB

log = structlog.get_logger(__name__)


class WatchManager:
    """Manages background anomaly-watch tasks for the server process."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}  # watch_id → Task
        self._db: TinkerDB | None = None

    def _get_db(self) -> TinkerDB:
        if self._db is None:
            self._db = TinkerDB()
        return self._db

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Called at server startup — resume any watches that were running."""
        db = self._get_db()
        running = db.list_watches(status="running")
        for w in running:
            self._launch(
                watch_id=w["watch_id"],
                service=w["service"],
                slack_channel=w.get("slack_channel"),
                interval_seconds=w["interval_seconds"],
            )
        if running:
            log.info("watch_manager.resumed", count=len(running))

    async def stop(self) -> None:
        """Called at server shutdown — cancel all running tasks."""
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        if self._db:
            self._db.close()
        log.info("watch_manager.stopped", count=len(self._tasks))

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def create(
        self,
        service: str,
        slack_channel: str | None = None,
        interval_seconds: int = 60,
    ) -> dict[str, Any]:
        watch_id = f"watch-{uuid.uuid4().hex[:8]}"
        db = self._get_db()
        db.create_watch(
            watch_id=watch_id,
            service=service,
            slack_channel=slack_channel,
            interval_seconds=interval_seconds,
        )
        self._launch(watch_id, service, slack_channel, interval_seconds)
        log.info("watch.created", watch_id=watch_id, service=service)
        return db.get_watch(watch_id) or {}

    def stop_watch(self, watch_id: str) -> bool:
        """Cancel the task and mark DB record stopped. Returns True if found."""
        task = self._tasks.pop(watch_id, None)
        if task:
            task.cancel()
        db = self._get_db()
        ok = db.stop_watch(watch_id)
        if ok:
            log.info("watch.stopped", watch_id=watch_id)
        return ok

    def list_all(self) -> list[dict[str, Any]]:
        return self._get_db().list_watches()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _launch(
        self,
        watch_id: str,
        service: str,
        slack_channel: str | None,
        interval_seconds: int,
    ) -> None:
        task = asyncio.create_task(
            self._watch_loop(watch_id, service, slack_channel, interval_seconds),
            name=f"watch-{watch_id}",
        )
        task.add_done_callback(lambda t: self._tasks.pop(watch_id, None))
        self._tasks[watch_id] = task

    async def _watch_loop(
        self,
        watch_id: str,
        service: str,
        slack_channel: str | None,
        interval_seconds: int,
    ) -> None:
        from tinker.backends import get_backend

        db = self._get_db()
        record = db.get_watch(watch_id) or {}
        last_hash = record.get("last_anomaly_hash") or ""

        log.info("watch.loop.start", watch_id=watch_id, service=service)

        while True:
            try:
                await asyncio.sleep(interval_seconds)
                backend = get_backend()
                anomalies = await backend.detect_anomalies(service)

                current_hash = _anomaly_hash(anomalies)
                db.update_watch(
                    watch_id,
                    last_run_at=_now(),
                    last_anomaly_hash=current_hash,
                )

                if current_hash != last_hash and anomalies:
                    await _post_slack(anomalies, service, slack_channel, watch_id)
                    last_hash = current_hash

            except asyncio.CancelledError:
                log.info("watch.loop.cancelled", watch_id=watch_id)
                break
            except Exception as exc:
                log.warning("watch.loop.error", watch_id=watch_id, error=str(exc))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _anomaly_hash(anomalies: list) -> str:
    key = json.dumps(
        sorted(
            [(a.service, a.metric, a.severity) for a in anomalies]
        ),
        sort_keys=True,
    )
    return hashlib.sha256(key.encode()).hexdigest()[:16]


async def _post_slack(anomalies: list, service: str, channel: str | None, watch_id: str) -> None:
    try:
        from tinker.config import settings
        token = settings.slack_bot_token
        if not token:
            return
        ch = channel or settings.slack_alerts_channel
        from slack_sdk.web.async_client import AsyncWebClient
        client = AsyncWebClient(token=token.get_secret_value())

        lines = [f"*Tinker Watch* — `{service}`  [{watch_id}]", ""]
        for a in anomalies[:5]:
            lines.append(f"• *{a.severity.upper()}* `{a.metric}` — {a.description}")
        if len(anomalies) > 5:
            lines.append(f"_…and {len(anomalies) - 5} more_")

        await client.chat_postMessage(channel=ch, text="\n".join(lines))
        log.info("watch.slack.posted", watch_id=watch_id, channel=ch, count=len(anomalies))
    except Exception as exc:
        log.warning("watch.slack.failed", watch_id=watch_id, error=str(exc))


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

"""SQLite persistence for interactive sessions and background watch state.

Schema
------
sessions  — REPL session context (anomalies, current focus, pending fix)
watches   — Background watch daemons (PID, service, Slack channel, last run)

Location: ~/.tinker/tinker.db  (overridable via TINKER_DB_PATH)
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _default_db_path() -> Path:
    tinker_dir = Path.home() / ".tinker"
    tinker_dir.mkdir(parents=True, exist_ok=True)
    return tinker_dir / "tinker.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    service      TEXT NOT NULL,
    anomalies    TEXT NOT NULL DEFAULT '[]',
    focus_idx    INTEGER,
    pending_fix  TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watches (
    watch_id          TEXT PRIMARY KEY,
    service           TEXT NOT NULL,
    pid               INTEGER NOT NULL,
    started_at        TEXT NOT NULL,
    slack_channel     TEXT,
    last_run_at       TEXT,
    last_anomaly_hash TEXT,
    interval_seconds  INTEGER NOT NULL DEFAULT 60,
    status            TEXT NOT NULL DEFAULT 'running'
);
"""


class TinkerDB:
    """SQLite-backed store for Tinker session and watch daemon state.

    Thread-safe for the common case of one writer at a time (CLI process).
    Uses ``check_same_thread=False`` because asyncio may call from a thread
    pool executor.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            from tinker.config import settings
            env_path = getattr(settings, "tinker_db_path", None)
            db_path = Path(env_path) if env_path else _default_db_path()
        self._path = Path(db_path)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ── Sessions ──────────────────────────────────────────────────────────────

    def create_session(self, service: str, anomalies: list[dict]) -> str:
        """Persist a new REPL session and return its session_id."""
        session_id = f"sess-{uuid.uuid4().hex[:8]}"
        now = _now()
        self._conn.execute(
            "INSERT INTO sessions (session_id, service, anomalies, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (session_id, service, json.dumps(anomalies, default=str), now, now),
        )
        self._conn.commit()
        return session_id

    def get_session(self, session_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["anomalies"] = json.loads(d["anomalies"] or "[]")
        if d["pending_fix"]:
            d["pending_fix"] = json.loads(d["pending_fix"])
        return d

    def update_session(self, session_id: str, **kwargs: Any) -> None:
        kwargs["updated_at"] = _now()
        if "anomalies" in kwargs and isinstance(kwargs["anomalies"], list):
            kwargs["anomalies"] = json.dumps(kwargs["anomalies"], default=str)
        if "pending_fix" in kwargs and isinstance(kwargs["pending_fix"], dict):
            kwargs["pending_fix"] = json.dumps(kwargs["pending_fix"], default=str)
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        self._conn.execute(
            f"UPDATE sessions SET {sets} WHERE session_id = ?",
            (*kwargs.values(), session_id),
        )
        self._conn.commit()

    def clean_sessions(self, older_than_hours: int = 24) -> int:
        """Delete sessions older than *older_than_hours*. Returns count removed."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).isoformat()
        cur = self._conn.execute("DELETE FROM sessions WHERE created_at < ?", (cutoff,))
        self._conn.commit()
        return cur.rowcount

    # ── Watches ───────────────────────────────────────────────────────────────

    def create_watch(
        self,
        service: str,
        pid: int,
        slack_channel: str | None = None,
        interval_seconds: int = 60,
    ) -> str:
        """Register a new background watch daemon. Returns watch_id."""
        watch_id = f"watch-{uuid.uuid4().hex[:8]}"
        self._conn.execute(
            "INSERT INTO watches"
            " (watch_id, service, pid, started_at, slack_channel, interval_seconds)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (watch_id, service, pid, _now(), slack_channel, interval_seconds),
        )
        self._conn.commit()
        return watch_id

    def get_watch(self, watch_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM watches WHERE watch_id = ?", (watch_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_watches(self, status: str = "running") -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM watches WHERE status = ? ORDER BY started_at DESC",
            (status,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_watch(self, watch_id: str, **kwargs: Any) -> None:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        self._conn.execute(
            f"UPDATE watches SET {sets} WHERE watch_id = ?",
            (*kwargs.values(), watch_id),
        )
        self._conn.commit()

    def stop_watch(self, watch_id: str) -> bool:
        """Signal the watch process to stop. Returns True if signal was sent."""
        row = self._conn.execute(
            "SELECT pid FROM watches WHERE watch_id = ? AND status = 'running'",
            (watch_id,),
        ).fetchone()
        if not row:
            return False
        try:
            import signal
            os.kill(row["pid"], signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        self.update_watch(watch_id, status="stopped")
        return True

    def clean_watches(self) -> int:
        """Remove stopped watches and those whose PID is dead. Returns count removed."""
        dead: list[str] = []
        for row in self._conn.execute(
            "SELECT watch_id, pid FROM watches WHERE status = 'running'"
        ).fetchall():
            try:
                os.kill(row["pid"], 0)  # signal 0 = existence check only
            except (ProcessLookupError, PermissionError):
                dead.append(row["watch_id"])

        if dead:
            placeholders = ",".join("?" * len(dead))
            self._conn.execute(
                f"UPDATE watches SET status = 'dead' WHERE watch_id IN ({placeholders})",
                dead,
            )

        cur = self._conn.execute(
            "DELETE FROM watches WHERE status IN ('stopped', 'dead')"
        )
        self._conn.commit()
        return len(dead) + cur.rowcount


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

"""SQLite persistence for interactive sessions and server-side watch state.

Schema
------
sessions  — REPL session context (anomalies, current focus, pending fix)
watches   — Watch tasks managed by the server (no PID — asyncio tasks, not processes)

Location: ~/.tinkr/tinker.db  (overridable via TINKR_DB_PATH)
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _default_db_path() -> Path:
    tinker_dir = Path.home() / ".tinkr"
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
    started_at        TEXT NOT NULL,
    slack_channel     TEXT,
    notifier          TEXT,
    destination       TEXT,
    last_run_at       TEXT,
    last_anomaly_hash TEXT,
    interval_seconds  INTEGER NOT NULL DEFAULT 60,
    status            TEXT NOT NULL DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS alert_rules (
    alert_id    TEXT PRIMARY KEY,
    service     TEXT NOT NULL,
    metric      TEXT NOT NULL,
    operator    TEXT NOT NULL,
    threshold   REAL NOT NULL,
    severity    TEXT NOT NULL DEFAULT 'medium',
    notifier    TEXT,
    destination TEXT,
    muted_until TEXT,
    created_at  TEXT NOT NULL
);
"""


class TinkerDB:
    """SQLite-backed store for Tinker session and watch state.

    Uses ``check_same_thread=False`` because asyncio may call from a thread
    pool executor on the server.
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
        self._migrate()

    def _migrate(self) -> None:
        """Apply additive schema migrations for existing databases."""
        migrations = [
            "ALTER TABLE watches ADD COLUMN notifier TEXT",
            "ALTER TABLE watches ADD COLUMN destination TEXT",
        ]
        for sql in migrations:
            try:
                self._conn.execute(sql)
            except sqlite3.OperationalError:
                pass  # column already exists
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ── Sessions ──────────────────────────────────────────────────────────────

    def create_session(self, service: str, anomalies: list[dict]) -> str:
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
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).isoformat()
        cur = self._conn.execute("DELETE FROM sessions WHERE created_at < ?", (cutoff,))
        self._conn.commit()
        return cur.rowcount

    # ── Watches ───────────────────────────────────────────────────────────────

    def create_watch(
        self,
        watch_id: str,
        service: str,
        notifier: str | None = None,
        destination: str | None = None,
        interval_seconds: int = 60,
    ) -> str:
        self._conn.execute(
            "INSERT INTO watches"
            " (watch_id, service, started_at, notifier, destination, interval_seconds)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (watch_id, service, _now(), notifier, destination, interval_seconds),
        )
        self._conn.commit()
        return watch_id

    def get_watch(self, watch_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM watches WHERE watch_id = ?", (watch_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_watches(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM watches WHERE status = ? ORDER BY started_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM watches ORDER BY started_at DESC"
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
        row = self._conn.execute(
            "SELECT watch_id FROM watches WHERE watch_id = ? AND status = 'running'",
            (watch_id,),
        ).fetchone()
        if not row:
            return False
        self.update_watch(watch_id, status="stopped")
        return True

    def delete_watch(self, watch_id: str) -> bool:
        """Hard-delete a watch record regardless of status. Returns True if found."""
        row = self._conn.execute(
            "SELECT watch_id FROM watches WHERE watch_id = ?", (watch_id,)
        ).fetchone()
        if not row:
            return False
        self._conn.execute("DELETE FROM watches WHERE watch_id = ?", (watch_id,))
        self._conn.commit()
        return True

    def clean_watches(self) -> int:
        cur = self._conn.execute(
            "DELETE FROM watches WHERE status IN ('stopped', 'dead')"
        )
        self._conn.commit()
        return cur.rowcount

    # ── Alert rules ───────────────────────────────────────────────────────────

    def create_alert(
        self,
        service: str,
        metric: str,
        operator: str,
        threshold: float,
        severity: str = "medium",
        notifier: str | None = None,
        destination: str | None = None,
    ) -> dict:
        alert_id = f"alert-{uuid.uuid4().hex[:8]}"
        now = _now()
        self._conn.execute(
            "INSERT INTO alert_rules"
            " (alert_id, service, metric, operator, threshold, severity, notifier, destination, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (alert_id, service, metric, operator, threshold, severity, notifier, destination, now),
        )
        self._conn.commit()
        return self.get_alert(alert_id) or {}

    def get_alert(self, alert_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM alert_rules WHERE alert_id = ?", (alert_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_alerts(self, service: str | None = None) -> list[dict]:
        if service:
            rows = self._conn.execute(
                "SELECT * FROM alert_rules WHERE service = ? ORDER BY created_at DESC", (service,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM alert_rules ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_alert(self, alert_id: str) -> bool:
        row = self._conn.execute(
            "SELECT alert_id FROM alert_rules WHERE alert_id = ?", (alert_id,)
        ).fetchone()
        if not row:
            return False
        self._conn.execute("DELETE FROM alert_rules WHERE alert_id = ?", (alert_id,))
        self._conn.commit()
        return True

    def mute_alert(self, alert_id: str, muted_until: str) -> bool:
        row = self._conn.execute(
            "SELECT alert_id FROM alert_rules WHERE alert_id = ?", (alert_id,)
        ).fetchone()
        if not row:
            return False
        self._conn.execute(
            "UPDATE alert_rules SET muted_until = ? WHERE alert_id = ?", (muted_until, alert_id)
        )
        self._conn.commit()
        return True


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

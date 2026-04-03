"""In-memory session store with TTL. Swap for Redis in production."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tinker.agent.orchestrator import AgentSession

_TTL = timedelta(hours=4)


class SessionStore:
    def __init__(self) -> None:
        self._by_id: dict[str, AgentSession] = {}
        self._by_incident: dict[str, str] = {}  # incident_id → session_id
        self._created: dict[str, datetime] = {}

    def put(self, session: AgentSession) -> None:
        self._evict_expired()
        self._by_id[session.session_id] = session
        self._created[session.session_id] = datetime.now(timezone.utc)

    def get(self, session_id: str) -> AgentSession | None:
        return self._by_id.get(session_id)

    def get_by_incident(self, incident_id: str) -> AgentSession | None:
        sid = self._by_incident.get(incident_id)
        return self._by_id.get(sid) if sid else None

    def index_incident(self, incident_id: str, session_id: str) -> None:
        self._by_incident[incident_id] = session_id

    def _evict_expired(self) -> None:
        now = datetime.now(timezone.utc)
        expired = [k for k, t in self._created.items() if now - t > _TTL]
        for k in expired:
            self._by_id.pop(k, None)
            self._created.pop(k, None)

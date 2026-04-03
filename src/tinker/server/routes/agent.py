"""Agent REST + SSE routes.

POST /api/v1/analyze   — stream RCA analysis as server-sent events
POST /api/v1/fix       — get fix suggestion for a prior incident
POST /api/v1/approve   — apply fix + open PR
GET  /api/v1/sessions/{id} — fetch session state
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, AsyncIterator

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from tinker.agent.guardrails import GuardRailChain
from tinker.agent.orchestrator import AgentSession, Orchestrator
from tinker.backends import get_backend
from tinker.server.auth import AuthContext, require_auth
from tinker.server.session_store import SessionStore

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["agent"])

# In-memory session store — swap for Redis in production
_store = SessionStore()


# ── Request / response models ─────────────────────────────────────────────────


class AnalyzeRequest(BaseModel):
    service: str
    since: str = "1h"
    deep: bool = False  # use claude-opus with thinking


class FixRequest(BaseModel):
    incident_id: str


class ApproveRequest(BaseModel):
    incident_id: str


# ── SSE helpers ───────────────────────────────────────────────────────────────


def _sse(event: str, data: str | dict) -> str:
    payload = data if isinstance(data, str) else json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"


async def _stream_analysis(
    service: str,
    since: str,
    session: AgentSession,
    orch: Orchestrator,
) -> AsyncIterator[str]:
    yield _sse("start", {"session_id": session.session_id, "service": service})
    try:
        async for chunk in orch.stream_analyze(service, since, session):
            yield _sse("chunk", {"text": chunk})

        report = session.incident_report
        if report:
            yield _sse("report", report.to_dict())
    except Exception as exc:
        log.exception("agent.stream_error", service=service)
        yield _sse("error", {"message": str(exc)})
    finally:
        yield _sse("done", {})


# ── Routes ────────────────────────────────────────────────────────────────────


@router.post("/analyze")
async def analyze(
    req: AnalyzeRequest,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> StreamingResponse:
    """Stream RCA analysis as server-sent events.

    The client reads the stream and processes events:
      event: start   — session started
      event: chunk   — text token from agent
      event: report  — final IncidentReport JSON
      event: error   — error occurred
      event: done    — stream complete
    """
    backend = get_backend()
    guardrails = GuardRailChain()
    orch = Orchestrator(use_deep_rca=req.deep)
    session = AgentSession(service=req.service)
    session.context["actor"] = auth.subject
    session.context["actor_roles"] = auth.roles

    _store.put(session)
    log.info("analyze.start", service=req.service, actor=auth.subject)

    return StreamingResponse(
        _stream_analysis(req.service, req.since, session, orch),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )


@router.post("/fix")
async def get_fix(
    req: FixRequest,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> dict:
    """Retrieve the pending fix for an incident."""
    session = _store.get_by_incident(req.incident_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Incident {req.incident_id} not found")

    report = session.incident_report
    if not report:
        raise HTTPException(status_code=404, detail="No incident report for this session")

    return {
        "incident_id": req.incident_id,
        "suggested_fix": report.suggested_fix,
        "fix_diff": report.fix_diff,
        "status": "pending_approval",
        "approve_hint": f"POST /api/v1/approve with incident_id={req.incident_id}",
    }


@router.post("/approve")
async def approve_fix(
    req: ApproveRequest,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> dict:
    """Apply a staged fix and open a PR. Requires oncall role."""
    if "oncall" not in auth.roles and "sre-lead" not in auth.roles:
        raise HTTPException(status_code=403, detail="Approving fixes requires oncall or sre-lead role")

    session = _store.get_by_incident(req.incident_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Incident {req.incident_id} not found")

    from tinker.agent.guardrails import GuardRailChain
    from tinker.agent.tools import ToolDispatcher
    from tinker.config import settings

    guardrails = GuardRailChain()
    guardrails.grant_approval(session.context, "apply_fix", auth.subject)

    dispatcher = ToolDispatcher(guardrails=guardrails, repo_path=settings.tinker_repo_path)
    result = await dispatcher.dispatch(
        "apply_fix",
        {"incident_id": req.incident_id},
        session.context,
    )

    log.info("fix.approved", incident_id=req.incident_id, approved_by=auth.subject)
    return result


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> dict:
    session = _store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    report = session.incident_report
    return {
        "session_id": session_id,
        "service": session.service,
        "incident": report.to_dict() if report else None,
    }

"""Watch management routes.

POST   /api/v1/watches            — create a watch
GET    /api/v1/watches            — list all watches
DELETE /api/v1/watches/{watch_id} — stop a watch

Creating a watch
----------------
Specify which notifier to use (must be defined in [notifiers.*] in config.toml):

    POST /api/v1/watches
    {
      "service": "payments-api",
      "notifier": "default",          // optional; "default" used if omitted
      "destination": "#payments-ops", // optional; notifier's own default used if omitted
      "interval_seconds": 60
    }

``destination`` meaning depends on the notifier type:
  - slack   → Slack channel name or ID (e.g. "#incidents")
  - discord → ignored (webhook URL is fixed at notifier config)
  - webhook → overrides the configured URL
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from tinker.server.auth import AuthContext, require_auth
from tinker.server.watch_manager import WatchManager

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/watches", tags=["watches"])

# Injected at app startup — see app.py
_manager: WatchManager | None = None


def set_manager(m: WatchManager) -> None:
    global _manager
    _manager = m


def _get_manager() -> WatchManager:
    if _manager is None:
        raise RuntimeError("WatchManager not initialised")
    return _manager


class CreateWatchRequest(BaseModel):
    service: str
    notifier: str | None = None       # name from [notifiers.*] in config.toml
    destination: str | None = None    # platform-specific target override
    interval_seconds: int = 60


@router.post("", status_code=201)
async def create_watch(
    req: CreateWatchRequest,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> dict[str, Any]:
    m = _get_manager()
    watch = m.create(
        service=req.service,
        notifier=req.notifier,
        destination=req.destination,
        interval_seconds=req.interval_seconds,
    )
    log.info("watch.created_via_api", service=req.service, notifier=req.notifier, actor=auth.subject)
    return watch


@router.get("")
async def list_watches(
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> dict[str, Any]:
    m = _get_manager()
    return {"watches": m.list_all()}


@router.delete("/{watch_id}")
async def stop_watch(
    watch_id: str,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> dict[str, Any]:
    m = _get_manager()
    ok = m.stop_watch(watch_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Watch {watch_id!r} not found or already stopped")
    log.info("watch.stopped_via_api", watch_id=watch_id, actor=auth.subject)
    return {"status": "stopped", "watch_id": watch_id}

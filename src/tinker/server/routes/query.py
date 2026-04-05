"""Raw observability query endpoints — used by the CLI in server mode.

POST /api/v1/logs        — query log entries
POST /api/v1/metrics     — get metric time series
POST /api/v1/anomalies   — detect anomalies
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from tinker.backends import get_backend_for_service
from tinker.backends.base import ServiceNotFoundError
from tinker.server.auth import AuthContext, require_auth

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["query"])


# ── Request models ────────────────────────────────────────────────────────────

class LogsRequest(BaseModel):
    service: str
    query: str = "*"
    start: datetime
    end: datetime
    limit: int = 100
    resource_type: str | None = None


class MetricsRequest(BaseModel):
    service: str
    metric: str
    start: datetime
    end: datetime


class AnomaliesRequest(BaseModel):
    service: str
    window_minutes: int = 10


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/logs")
async def query_logs(
    req: LogsRequest,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> dict[str, Any]:
    backend = get_backend_for_service(req.service)
    try:
        entries = await backend.query_logs(req.service, req.query, req.start, req.end, req.limit, req.resource_type)
    except ServiceNotFoundError as exc:
        log.warning("query.logs.service_not_found", service=req.service)
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        log.error("query.logs.error", service=req.service, error=str(exc))
        raise HTTPException(status_code=502, detail=f"Backend error: {exc}")
    log.debug("query.logs", service=req.service, count=len(entries), actor=auth.subject)
    return {
        "entries": [
            {
                "timestamp": e.timestamp.isoformat(),
                "level": e.level,
                "message": e.message,
                "service": e.service,
                "trace_id": e.trace_id,
                "span_id": e.span_id,
                "extra": e.extra,
            }
            for e in entries
        ]
    }


@router.post("/metrics")
async def get_metrics(
    req: MetricsRequest,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> dict[str, Any]:
    backend = get_backend_for_service(req.service)
    try:
        points = await backend.get_metrics(req.service, req.metric, req.start, req.end)
    except ServiceNotFoundError as exc:
        log.warning("query.metrics.service_not_found", service=req.service)
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        log.error("query.metrics.error", service=req.service, metric=req.metric, error=str(exc))
        raise HTTPException(status_code=502, detail=f"Backend error: {exc}")
    log.debug("query.metrics", service=req.service, metric=req.metric, count=len(points))
    return {
        "points": [
            {
                "timestamp": p.timestamp.isoformat(),
                "value": p.value,
                "unit": p.unit,
                "dimensions": p.dimensions,
            }
            for p in points
        ]
    }


@router.post("/anomalies")
async def detect_anomalies(
    req: AnomaliesRequest,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> dict[str, Any]:
    backend = get_backend_for_service(req.service)
    try:
        anomalies = await backend.detect_anomalies(req.service, req.window_minutes)
    except ServiceNotFoundError as exc:
        log.warning("query.anomalies.service_not_found", service=req.service)
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        log.error("query.anomalies.error", service=req.service, error=str(exc))
        raise HTTPException(status_code=502, detail=f"Backend error: {exc}")
    log.debug("query.anomalies", service=req.service, count=len(anomalies))
    return {
        "anomalies": [a.to_dict() for a in anomalies]
    }

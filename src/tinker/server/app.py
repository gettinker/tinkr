"""Tinker Agent Server — FastAPI application factory."""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from tinker import __version__

log = structlog.get_logger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Tinker Agent Server",
        description="AI-powered observability and incident response agent",
        version=__version__,
        docs_url="/docs",
        redoc_url=None,
    )

    # ── CORS (tighten origins for production) ─────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Request logging ───────────────────────────────────────────────────────
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        response = await call_next(request)
        log.info(
            "http.request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
        )
        return response

    # ── Global error handler ──────────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception):
        log.exception("http.unhandled_error", path=request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    # ── Routes ────────────────────────────────────────────────────────────────
    from tinker.server.routes.agent import router as agent_router
    from tinker.server.routes.mcp import router as mcp_router

    app.include_router(agent_router)
    app.include_router(mcp_router)

    # ── Slack bot (mounted as ASGI sub-app) ───────────────────────────────────
    try:
        from tinker.server.slack_handler import build_slack_app
        slack_handler = build_slack_app()
        app.mount("/slack", slack_handler)
        log.info("slack.mounted")
    except Exception:
        log.warning("slack.not_configured")

    # ── Health ────────────────────────────────────────────────────────────────
    @app.get("/health", tags=["ops"])
    async def health():
        from tinker.config import settings
        return {
            "status": "ok",
            "version": __version__,
            "backend": settings.tinker_backend,
        }

    return app


def main() -> None:
    import uvicorn
    from tinker.config import settings

    uvicorn.run(
        "tinker.server.app:create_app",
        factory=True,
        host=settings.tinker_server_host,
        port=settings.tinker_server_port,
        log_level=settings.log_level.lower(),
    )

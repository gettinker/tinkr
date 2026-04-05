"""Tinker Agent Server — FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from tinker import __version__

log = structlog.get_logger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    from tinker.server.notifiers import NotifierRegistry
    from tinker.server.watch_manager import WatchManager
    from tinker.server.routes.watches import set_manager
    from tinker import toml_config as tc

    registry = NotifierRegistry()
    cfg = tc.get()
    if cfg.notifiers:
        registry.build_from_toml(cfg.notifiers)
        log.info("notifiers.loaded", count=len(registry))
    else:
        log.info("notifiers.none_configured — watches will fall back to legacy Slack settings")

    manager = WatchManager(registry=registry)
    set_manager(manager)
    await manager.start()
    yield
    await manager.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        lifespan=_lifespan,
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
    from tinker.server.routes.query import router as query_router
    from tinker.server.routes.watches import router as watches_router

    app.include_router(agent_router)
    app.include_router(query_router)
    app.include_router(mcp_router)
    app.include_router(watches_router)

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
        from tinker import toml_config as tc
        cfg = tc.get()
        if cfg.backends:
            backends_info = {name: b.type for name, b in cfg.backends.items()}
        else:
            backends_info = {"default": settings.tinker_backend}
        return {
            "status": "ok",
            "version": __version__,
            "backend": settings.tinker_backend,   # kept for CLI compat
            "backends": backends_info,
        }

    return app


def main() -> None:
    import uvicorn
    from tinker.config import settings
    from tinker import toml_config as tc

    toml = tc.get()
    host = toml.server.host if toml.backends else settings.tinker_server_host
    port = toml.server.port if toml.backends else settings.tinker_server_port
    log_level = toml.server.log_level if toml.backends else settings.log_level.lower()

    uvicorn.run(
        "tinker.server.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level=log_level,
    )

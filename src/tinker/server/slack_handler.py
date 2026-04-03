"""Slack Bolt app wrapped as an ASGI handler for mounting into FastAPI."""

from __future__ import annotations

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler

from tinker.config import settings


def build_slack_app() -> AsyncSlackRequestHandler:
    """Return a FastAPI-compatible Slack request handler."""
    token = settings.slack_bot_token
    secret = settings.slack_signing_secret
    if not token or not secret:
        raise RuntimeError("SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET must be set")

    # Re-use the existing Bolt app definition from the interfaces module
    from tinker.interfaces.slack_bot import build_app
    bolt_app: AsyncApp = build_app()

    return AsyncSlackRequestHandler(bolt_app)

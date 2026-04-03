"""Tinker Agent Server — FastAPI application.

Exposes:
  POST /api/v1/analyze          → run RCA, stream results
  POST /api/v1/fix              → get fix suggestion
  POST /api/v1/approve          → apply fix + open PR
  GET  /api/v1/sessions/{id}    → session state
  GET  /mcp                     → MCP over SSE (for Claude Code remote config)
  POST /slack/events            → Slack Bolt event handler
  GET  /health                  → liveness probe
"""

from tinker.server.app import create_app

__all__ = ["create_app"]

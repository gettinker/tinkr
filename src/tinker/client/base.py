"""Tinker client interface.

There is one client implementation: RemoteClient (talks to a Tinker server).
This module re-exports it and the factory function for convenience.
"""

from __future__ import annotations

from tinker.client.remote import RemoteClient
from tinker.client.config import resolve


def get_client(url_override: str | None = None) -> RemoteClient:
    """Return a RemoteClient configured from environment / ~/.tinker/config."""
    cfg = resolve(url_override=url_override)
    return RemoteClient(cfg)


__all__ = ["RemoteClient", "get_client"]

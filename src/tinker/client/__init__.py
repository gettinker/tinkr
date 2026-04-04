"""Tinker client — always talks to a Tinker server over HTTP.

Usage:
    from tinker.client import get_client

    client = get_client()                          # reads ~/.tinker/config
    client = get_client("http://localhost:8000")   # explicit URL override
"""

from tinker.client.base import RemoteClient, get_client

__all__ = ["RemoteClient", "get_client"]

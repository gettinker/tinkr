"""Client-side configuration.

Resolution order for server URL:
  1. Explicit override passed to resolve()
  2. TINKR_SERVER_URL env var
  3. url in ~/.tinkr/config (TOML)
  4. Fallback: http://localhost:8000

The API token is always read from the TINKR_API_TOKEN env var.
~/.tinkr/config stores the URL only — never secrets.

~/.tinkr/config format
-----------------------
url = "https://tinker.internal"
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ServerConfig:
    url: str
    api_key_env: str = "TINKR_API_TOKEN"

    @property
    def api_key(self) -> str:
        # Env var takes priority over stored config (allows per-session override)
        key = os.environ.get(self.api_key_env, "") or _read_config().get("token", "")
        if not key:
            raise RuntimeError(
                "Tinker API token not set.\n"
                "Run: tinker init cli"
            )
        return key


def resolve(url_override: str | None = None) -> ServerConfig:
    """Return the effective server config."""
    cfg = _read_config()
    url = (
        url_override
        or os.environ.get("TINKR_SERVER_URL", "")
        or cfg.get("url", "")
        or "http://localhost:8000"
    )
    return ServerConfig(url=url.rstrip("/"))


def _config_path() -> Path:
    return Path.home() / ".tinkr" / "config"


def _read_config() -> dict:
    path = _config_path()
    if not path.exists():
        return {}
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-reuse-def]
        return tomllib.loads(path.read_text())
    except Exception:
        return {}


def write_config(url: str, token: str | None = None) -> Path:
    """Write server URL (and optionally token) to ~/.tinkr/config."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Preserve any existing keys we're not updating
    existing = _read_config()
    existing["url"] = url
    if token:
        existing["token"] = token

    lines = [f'{k} = "{v}"' for k, v in existing.items()]
    path.write_text("\n".join(lines) + "\n")
    return path

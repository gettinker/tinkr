"""Client-side configuration.

Resolution order for server URL:
  1. Explicit override passed to resolve()
  2. TINKER_SERVER_URL env var
  3. url in ~/.tinker/config (TOML)
  4. Fallback: http://localhost:8000

The API token is always read from the TINKER_API_TOKEN env var.
~/.tinker/config stores the URL only — never secrets.

~/.tinker/config format
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
    api_key_env: str = "TINKER_API_TOKEN"

    @property
    def api_key(self) -> str:
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise RuntimeError(
                f"Tinker API token not set.\n"
                f"Export {self.api_key_env}=<your-token> or run: tinker init cli"
            )
        return key


def resolve(url_override: str | None = None) -> ServerConfig:
    """Return the effective server config."""
    url = (
        url_override
        or os.environ.get("TINKER_SERVER_URL", "")
        or _read_config_url()
        or "http://localhost:8000"
    )
    return ServerConfig(url=url.rstrip("/"))


def _read_config_url() -> str:
    path = _config_path()
    if not path.exists():
        return ""
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-reuse-def]
        data = tomllib.loads(path.read_text())
        return data.get("url", "")
    except Exception:
        return ""


def _config_path() -> Path:
    return Path.home() / ".tinker" / "config"


def write_config(url: str) -> Path:
    """Write the server URL to ~/.tinker/config."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'url = "{url}"\n')
    return path

"""Profile management endpoints.

POST /api/v1/profiles/{name}/activate
    Switch the active profile on the running server without a restart.
    Writes the new active_profile to config.toml, reloads the TomlConfig
    singleton, and clears the backend instance cache so the next request
    picks up a fresh backend pointed at the new profile.

GET /api/v1/profiles
    List all configured profiles and which one is currently active.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException

from tinker.server.auth import AuthContext, require_auth

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/profiles", tags=["profiles"])


@router.get("")
async def list_profiles(
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> dict:
    from tinker import toml_config as tc

    cfg = tc.get()
    active = cfg.active_profile or (next(iter(cfg.profiles)) if cfg.profiles else None)
    return {
        "active_profile": active,
        "profiles": {
            name: {"backend": p.backend, "active": name == active}
            for name, p in cfg.profiles.items()
        },
    }


@router.post("/{name}/activate")
async def activate_profile(
    name: str,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> dict:
    """Switch the active profile on the live server — no restart required.

    Steps:
    1. Validate the profile exists in the current config.
    2. Write the new active_profile to ~/.tinkr/config.toml (same as the CLI does).
    3. Reload the TomlConfig singleton so all subsequent requests see the new profile.
    4. Clear the backend instance cache so the new profile's backend is instantiated
       on the next request (not the cached one from the old profile).
    """
    import re
    from pathlib import Path

    from tinker import toml_config as tc
    from tinker.backends import clear_cache

    cfg = tc.get()

    if name not in cfg.profiles:
        available = ", ".join(cfg.profiles) or "none"
        raise HTTPException(
            status_code=404,
            detail=f"Profile '{name}' not found. Available: {available}",
        )

    # Write the new active_profile to disk (same logic as CLI _set_active_profile)
    toml_file = Path.home() / ".tinkr" / "config.toml"
    if not toml_file.exists():
        raise HTTPException(status_code=500, detail="config.toml not found on server")

    text = toml_file.read_text(encoding="utf-8")
    new_line = f'active_profile = "{name}"'
    if re.search(r"^active_profile\s*=", text, re.MULTILINE):
        text = re.sub(r"^active_profile\s*=.*$", new_line, text, flags=re.MULTILINE)
    else:
        lines = text.splitlines(keepends=True)
        insert_at = 0
        for i, line in enumerate(lines):
            if line.startswith("#") or line.strip() == "":
                insert_at = i + 1
            else:
                break
        lines.insert(insert_at, f"{new_line}\n\n")
        text = "".join(lines)
    toml_file.write_text(text, encoding="utf-8")

    # Reload config and evict the backend cache in this server process
    new_cfg = tc.reload()
    clear_cache()

    log.info("profile.activated", name=name, subject=auth.subject)
    return {
        "active_profile": name,
        "backend": new_cfg.profiles[name].backend,
        "message": f"Switched to profile '{name}' — no restart required.",
    }

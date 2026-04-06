"""Authentication middleware for the Tinker Agent Server.

Two schemes are supported:

1. API Key (simple, for CLI and service-to-service)
   Header: Authorization: Bearer <api-key>
   Keys are stored as bcrypt hashes in TINKER_API_KEYS env var (comma-separated).

2. Short-lived JWT (for human users authenticating via SSO/OIDC)
   Header: Authorization: Bearer <jwt>
   Issued by your IdP; Tinker validates signature + expiry + `tinker` audience claim.
   Set TINKER_JWT_JWKS_URL to your IdP's JWKS endpoint.

Deployment notes
----------------
- In AWS: attach an IAM role to the ECS task / Lambda.
  The server itself does NOT need AWS credentials — it inherits them from the role.
  API keys are for *clients* (CLI, Slack bot) authenticating to *Tinker*, not to AWS.

- In GCP: attach a service account to the Cloud Run instance.
  Same pattern — the server authenticates to GCP via Workload Identity,
  not via a key file.

- API keys should be rotated via your secrets manager.
  Tinker reads them at startup from the env — restart the server to pick up rotated keys.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Annotated

import structlog
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

log = structlog.get_logger(__name__)

_bearer = HTTPBearer(auto_error=True)


class AuthContext:
    """Resolved identity after auth succeeds."""

    def __init__(self, subject: str, roles: list[str], auth_method: str) -> None:
        self.subject = subject  # user ID or service name
        self.roles = roles
        self.auth_method = auth_method  # "api_key" | "jwt"

    def __repr__(self) -> str:
        return f"AuthContext(subject={self.subject!r}, roles={self.roles}, method={self.auth_method})"


# ── API key validation ────────────────────────────────────────────────────────

def _load_api_keys() -> dict[str, dict]:
    """Load API keys from config.toml [auth] (preferred) or TINKER_API_KEYS env var (legacy).

    config.toml format:
        [auth]
        api_keys = [{hash = "<sha256-hex>", subject = "cli-mohit", roles = ["oncall"]}]

    Legacy TINKER_API_KEYS format (JSON string in .env):
        [{"hash": "<sha256-hex>", "subject": "cli-mohit", "roles": ["sre"]}]
    """
    import json

    # Prefer config.toml [auth] entries
    try:
        from tinker import toml_config as tc
        cfg = tc.get()
        if cfg.auth.api_keys:
            return {entry.hash: {"hash": entry.hash, "subject": entry.subject, "roles": entry.roles}
                    for entry in cfg.auth.api_keys}
    except Exception:
        pass

    # Fall back to legacy TINKER_API_KEYS env var
    from tinker.config import settings
    raw = settings.tinker_api_keys
    try:
        entries = json.loads(raw)
        return {entry["hash"]: entry for entry in entries}
    except Exception:
        log.warning("auth.api_keys_parse_failed", raw=raw[:80])
        return {}


_API_KEYS: dict[str, dict] | None = None  # loaded lazily on first request


def _validate_api_key(token: str) -> AuthContext | None:
    global _API_KEYS
    if _API_KEYS is None:
        _API_KEYS = _load_api_keys()

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    entry = _API_KEYS.get(token_hash)
    if not entry:
        return None
    return AuthContext(
        subject=entry["subject"],
        roles=entry.get("roles", []),
        auth_method="api_key",
    )


# ── JWT validation ────────────────────────────────────────────────────────────

def _validate_jwt(token: str) -> AuthContext | None:
    jwks_url = os.environ.get("TINKER_JWT_JWKS_URL")
    audience = os.environ.get("TINKER_JWT_AUDIENCE", "tinker")
    if not jwks_url:
        return None

    try:
        import jwt
        from jwt import PyJWKClient

        jwks_client = PyJWKClient(jwks_url)
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=audience,
        )
        return AuthContext(
            subject=payload.get("sub", "unknown"),
            roles=payload.get("tinker_roles", []),
            auth_method="jwt",
        )
    except Exception as exc:
        log.debug("auth.jwt_invalid", reason=str(exc))
        return None


# ── FastAPI dependency ────────────────────────────────────────────────────────

async def require_auth(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> AuthContext:
    """FastAPI dependency — resolves to AuthContext or raises 401."""
    token = credentials.credentials

    # Try API key first (cheaper), then JWT
    ctx = _validate_api_key(token) or _validate_jwt(token)
    if ctx is None:
        log.warning("auth.rejected")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    log.info("auth.ok", subject=ctx.subject, method=ctx.auth_method)
    return ctx


# ── Slack request signature verification ─────────────────────────────────────

def verify_slack_signature(
    x_slack_signature: str,
    x_slack_request_timestamp: str,
    body: bytes,
) -> bool:
    """Verify Slack's HMAC-SHA256 request signature."""
    signing_secret = os.environ.get("SLACK_SIGNING_SECRET", "")
    if not signing_secret:
        log.error("auth.slack_signing_secret_missing")
        return False

    base = f"v0:{x_slack_request_timestamp}:{body.decode()}"
    expected = "v0=" + hmac.new(
        signing_secret.encode(), base.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, x_slack_signature)

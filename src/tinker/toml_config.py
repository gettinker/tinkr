"""TOML-based server configuration.

Layout
------
  ~/.tinker/config.toml   — structure (backends, services, server settings)
  ~/.tinker/.env          — secrets (API keys, tokens) — never committed

Secret references
-----------------
Any string value in config.toml that starts with ``env:`` is resolved from the
environment at load time:

    api_key = "env:GRAFANA_API_KEY"   →  os.environ["GRAFANA_API_KEY"]

If the referenced variable is not set the field is left as None (with a warning).

Fallback
--------
If config.toml does not exist the loader returns an empty TomlConfig so all
callers fall back to the existing pydantic-settings / .env behaviour.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_CONFIG_PATH = Path.home() / ".tinker" / "config.toml"


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class ServerSection:
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"


@dataclass
class LLMSection:
    default_model: str = "anthropic/claude-sonnet-4-6"
    deep_rca_model: str = "anthropic/claude-opus-4-6"


@dataclass
class ApiKeyEntry:
    hash: str
    subject: str
    roles: list[str] = field(default_factory=list)


@dataclass
class AuthSection:
    api_keys: list[ApiKeyEntry] = field(default_factory=list)


@dataclass
class BackendConfig:
    """Config for one named backend (e.g. [backends.prod])."""
    type: str                          # cloudwatch | grafana | gcp | azure | datadog | elastic | otel
    # All other keys stored verbatim (secrets already resolved)
    options: dict[str, str] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)


@dataclass
class ServiceConfig:
    """Per-service config (e.g. [services.payments-api])."""
    backend: str | None = None          # named backend; None → use default
    log_format: str = "label"           # label | json | logfmt | pattern
    log_level_field: str = "level"      # field name that holds the level value
    repo: str | None = None             # override github repo (owner/repo)
    resource_type: str | None = None    # ecs | lambda | eks | cloudrun | aks …


@dataclass
class SlackSection:
    bot_token: str | None = None
    alerts_channel: str = "#incidents"
    app_token: str | None = None
    signing_secret: str | None = None


@dataclass
class GitHubSection:
    token: str | None = None
    default_repo: str | None = None     # owner/repo


@dataclass
class TomlConfig:
    server: ServerSection = field(default_factory=ServerSection)
    llm: LLMSection = field(default_factory=LLMSection)
    auth: AuthSection = field(default_factory=AuthSection)
    backends: dict[str, BackendConfig] = field(default_factory=dict)
    services: dict[str, ServiceConfig] = field(default_factory=dict)
    slack: SlackSection = field(default_factory=SlackSection)
    github: GitHubSection = field(default_factory=GitHubSection)

    # Name of the default backend (first defined, or "default" key)
    _default_backend: str | None = field(default=None, repr=False)

    def get_service(self, service: str) -> ServiceConfig:
        """Return config for *service*, falling back to an empty ServiceConfig."""
        return self.services.get(service, ServiceConfig())

    def get_backend_config(self, name: str | None = None) -> BackendConfig | None:
        """Return named backend config, or the default backend if name is None."""
        if not self.backends:
            return None
        key = name or self._default_backend
        if key and key in self.backends:
            return self.backends[key]
        # Fall back to the first defined backend
        return next(iter(self.backends.values()))

    def get_backend_for_service(self, service: str) -> BackendConfig | None:
        """Return the backend config that should handle *service*."""
        svc = self.get_service(service)
        return self.get_backend_config(svc.backend)


# ── Secret resolution ─────────────────────────────────────────────────────────

def _resolve(value: Any) -> Any:
    """Resolve ``env:VAR`` references; pass other values through unchanged."""
    if not isinstance(value, str):
        return value
    if value.startswith("env:"):
        var = value[4:].strip()
        resolved = os.environ.get(var)
        if resolved is None:
            log.warning("toml_config.unresolved_secret", var=var)
        return resolved
    return value


def _resolve_dict(d: dict) -> dict:
    return {k: _resolve(v) for k, v in d.items()}


# ── Loader ────────────────────────────────────────────────────────────────────

def load(path: Path = _CONFIG_PATH) -> TomlConfig:
    """Parse *path* and return a TomlConfig.  Returns an empty config if the
    file does not exist (backward-compat with .env-only setups).
    """
    if not path.exists():
        return TomlConfig()

    try:
        import tomllib  # Python 3.11+
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            log.warning(
                "toml_config.tomllib_unavailable",
                msg="Install tomli (pip install tomli) or use Python 3.11+ to read config.toml",
            )
            return TomlConfig()

    try:
        raw: dict = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.error("toml_config.parse_error", path=str(path), error=str(exc))
        return TomlConfig()

    cfg = TomlConfig()

    # [server]
    if s := raw.get("server"):
        cfg.server = ServerSection(
            host=s.get("host", "0.0.0.0"),
            port=int(s.get("port", 8000)),
            log_level=s.get("log_level", "info"),
        )

    # [llm]
    if l := raw.get("llm"):
        cfg.llm = LLMSection(
            default_model=l.get("default_model", "anthropic/claude-sonnet-4-6"),
            deep_rca_model=l.get("deep_rca_model", "anthropic/claude-opus-4-6"),
        )

    # [auth]
    if a := raw.get("auth"):
        keys = [
            ApiKeyEntry(
                hash=k["hash"],
                subject=k.get("subject", "unknown"),
                roles=k.get("roles", []),
            )
            for k in a.get("api_keys", [])
        ]
        cfg.auth = AuthSection(api_keys=keys)

    # [backends.*]
    for name, b in raw.get("backends", {}).items():
        btype = b.pop("type", "")
        cfg.backends[name] = BackendConfig(
            type=btype,
            options=_resolve_dict(b),
        )
    if cfg.backends:
        cfg._default_backend = next(iter(cfg.backends))

    # [services.*]
    for name, s in raw.get("services", {}).items():
        cfg.services[name] = ServiceConfig(
            backend=s.get("backend"),
            log_format=s.get("log_format", "label"),
            log_level_field=s.get("log_level_field", "level"),
            repo=s.get("repo"),
            resource_type=s.get("resource_type"),
        )

    # [slack]
    if sl := raw.get("slack"):
        cfg.slack = SlackSection(
            bot_token=_resolve(sl.get("bot_token")),
            alerts_channel=sl.get("alerts_channel", "#incidents"),
            app_token=_resolve(sl.get("app_token")),
            signing_secret=_resolve(sl.get("signing_secret")),
        )

    # [github]
    if gh := raw.get("github"):
        cfg.github = GitHubSection(
            token=_resolve(gh.get("token")),
            default_repo=gh.get("default_repo"),
        )

    log.info(
        "toml_config.loaded",
        path=str(path),
        backends=list(cfg.backends),
        services=list(cfg.services),
    )
    return cfg


# ── Singleton ─────────────────────────────────────────────────────────────────

_instance: TomlConfig | None = None


def get() -> TomlConfig:
    """Return the process-wide TomlConfig singleton."""
    global _instance
    if _instance is None:
        _instance = load()
    return _instance


def reload() -> TomlConfig:
    """Force a reload from disk (useful in tests)."""
    global _instance
    _instance = load()
    return _instance

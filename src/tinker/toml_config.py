"""TOML-based server configuration.

Layout
------
  ~/.tinker/config.toml   — structure (profiles, services, notifiers, server settings)
  ~/.tinker/.env          — secrets (API keys, tokens) — never committed

Secret references
-----------------
Any string value in config.toml that starts with ``env:`` is resolved from the
environment at load time:

    api_key = "env:GRAFANA_API_KEY"   →  os.environ["GRAFANA_API_KEY"]

~/.tinker/.env is automatically loaded into os.environ before resolution so
secrets written by ``tinker init server`` are always available.
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
class ServiceConfig:
    """Per-service config (e.g. [services.payments-api])."""
    backend: str | None = None          # named backend; None → use default
    log_format: str = "label"           # label | json | logfmt | pattern
    log_level_field: str = "level"      # field name that holds the level value
    repo: str | None = None             # override github repo (owner/repo)
    resource_type: str | None = None    # ecs | lambda | eks | cloudrun | aks …


@dataclass
class NotifierConfig:
    """Config for one named notifier (e.g. [notifiers.default])."""
    type: str                            # slack | discord | webhook
    options: dict[str, str] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)


@dataclass
class ProfileConfig:
    """Config for one named profile (e.g. [profiles.aws-prod]).

    A profile bundles a backend with its services and notifiers so multiple
    cloud accounts can coexist in a single config.toml.
    """
    backend: str                                                  # cloudwatch | grafana | ...
    options: dict[str, str] = field(default_factory=dict)         # region, url, api_key, …
    services: dict[str, ServiceConfig] = field(default_factory=dict)
    notifiers: dict[str, NotifierConfig] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)


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
    profiles: dict[str, ProfileConfig] = field(default_factory=dict)
    active_profile: str | None = None
    slack: SlackSection = field(default_factory=SlackSection)
    github: GitHubSection = field(default_factory=GitHubSection)


    def active_profile_config(self) -> ProfileConfig | None:
        """Return the active ProfileConfig, or None if no profiles are defined."""
        if not self.profiles:
            return None
        key = self.active_profile or next(iter(self.profiles))
        return self.profiles.get(key)

    def get_notifiers(self) -> dict[str, NotifierConfig]:
        """Return notifiers from the active profile."""
        profile = self.active_profile_config()
        if profile:
            return profile.notifiers
        return {}

    def get_service(self, service: str) -> ServiceConfig:
        """Return config for *service* from the active profile."""
        profile = self.active_profile_config()
        if profile and service in profile.services:
            return profile.services[service]
        return ServiceConfig()


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

def _load_env_file_into_environ() -> None:
    """Load ~/.tinker/.env into os.environ so env: references resolve correctly.

    pydantic-settings reads the file into its own settings object but does NOT
    inject values into os.environ. toml_config._resolve() reads os.environ, so
    we need to bridge the gap ourselves. Existing env vars are never overwritten.
    """
    env_path = Path.home() / ".tinker" / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def load(path: Path = _CONFIG_PATH) -> TomlConfig:
    """Parse *path* and return a TomlConfig. Returns an empty config if the file does not exist."""
    _load_env_file_into_environ()

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

    # active_profile = "..."
    cfg.active_profile = raw.get("active_profile")

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

    # [profiles.*]  (new-style — takes precedence over legacy [backends.*])
    for name, p in raw.get("profiles", {}).items():
        backend = p.get("backend", p.get("type", ""))
        # Scalar options: everything except reserved structural keys
        _reserved = {"backend", "type", "services", "notifiers"}
        options = {
            k: _resolve(v) for k, v in p.items()
            if k not in _reserved and not isinstance(v, dict)
        }
        # Per-profile services
        profile_services: dict[str, ServiceConfig] = {}
        for sname, s in p.get("services", {}).items():
            profile_services[sname] = ServiceConfig(
                backend=s.get("backend"),
                log_format=s.get("log_format", "label"),
                log_level_field=s.get("log_level_field", "level"),
                repo=s.get("repo"),
                resource_type=s.get("resource_type"),
            )
        # Per-profile notifiers
        profile_notifiers: dict[str, NotifierConfig] = {}
        for nname, n in p.get("notifiers", {}).items():
            ntype = n.get("type", "")
            profile_notifiers[nname] = NotifierConfig(
                type=ntype,
                options=_resolve_dict({k: v for k, v in n.items() if k != "type"}),
            )
        cfg.profiles[name] = ProfileConfig(
            backend=backend,
            options=options,
            services=profile_services,
            notifiers=profile_notifiers,
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
        active_profile=cfg.active_profile,
        profiles=list(cfg.profiles),
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

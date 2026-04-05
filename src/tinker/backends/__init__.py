"""Observability backend registry.

Two operating modes
-------------------
Legacy (.env only)
    TINKER_BACKEND=grafana in the environment selects a single backend for all
    services. All backends read their config from pydantic-settings / .env.

TOML config (~/.tinker/config.toml)
    Named backends are declared under [backends.*].  Services are routed to
    their backend via [services.<name>].backend.  Backend instances are cached
    so each named backend is constructed once per process.

    Use get_backend_for_service(service) in route handlers.
    get_backend() is kept for backward compat and returns the default backend.
"""

from __future__ import annotations

import importlib
import structlog

from tinker.backends.base import ObservabilityBackend

log = structlog.get_logger(__name__)

# Lazy registry — values are import paths to avoid loading all SDKs at startup.
_REGISTRY: dict[str, str] = {
    "cloudwatch":    "tinker.backends.cloudwatch:CloudWatchBackend",
    "gcp":           "tinker.backends.gcp:GCPBackend",
    "azure":         "tinker.backends.azure:AzureBackend",
    "grafana":       "tinker.backends.grafana:GrafanaBackend",
    "datadog":       "tinker.backends.datadog:DatadogBackend",
    "elastic":       "tinker.backends.elastic:ElasticBackend",
    "elasticsearch": "tinker.backends.elastic:ElasticBackend",
    "opensearch":    "tinker.backends.elastic:ElasticBackend",
    "otel":          "tinker.backends.otel:OTelBackend",
}

# Cache of named backend instances (keyed by TOML backend name or type string)
_instances: dict[str, ObservabilityBackend] = {}


def _make_backend(type_key: str, config: dict | None = None) -> ObservabilityBackend:
    """Instantiate a backend by type key, passing optional TOML config dict."""
    key = type_key.lower()
    if key not in _REGISTRY:
        available = ", ".join(k for k in _REGISTRY if k not in ("elasticsearch", "opensearch"))
        raise ValueError(f"Unknown backend type '{key}'. Available: {available}")

    module_path, cls_name = _REGISTRY[key].split(":")
    module = importlib.import_module(module_path)
    cls = getattr(module, cls_name)
    return cls(config=config) if config is not None else cls()


def get_backend(name: str | None = None) -> ObservabilityBackend:
    """Return a backend by name or TINKER_BACKEND env var.

    In TOML mode this returns the default backend (first declared under
    [backends.*]).  Instances are cached per name/type.
    """
    from tinker import toml_config as tc
    cfg = tc.get()

    if cfg.backends:
        # TOML mode — use default backend
        backend_cfg = cfg.get_backend_config(name)
        if backend_cfg:
            cache_key = name or cfg._default_backend or backend_cfg.type
            if cache_key not in _instances:
                log.info("backend.init", name=cache_key, type=backend_cfg.type)
                _instances[cache_key] = _make_backend(backend_cfg.type, backend_cfg.options)
            return _instances[cache_key]

    # Legacy .env mode
    from tinker.config import settings
    key = (name or settings.tinker_backend).lower()
    if key not in _instances:
        log.info("backend.init", backend=key)
        _instances[key] = _make_backend(key)
    return _instances[key]


def get_backend_for_service(service: str) -> ObservabilityBackend:
    """Return the backend that should handle *service*.

    In TOML mode, looks up [services.<service>].backend and returns the
    corresponding cached backend instance.  Falls back to get_backend() when
    no TOML config exists or the service is not explicitly mapped.
    """
    from tinker import toml_config as tc
    cfg = tc.get()

    if cfg.backends:
        svc_cfg = cfg.get_service(service)
        backend_name = svc_cfg.backend or cfg._default_backend
        if backend_name and backend_name in cfg.backends:
            if backend_name not in _instances:
                b = cfg.backends[backend_name]
                log.info("backend.init", name=backend_name, type=b.type)
                _instances[backend_name] = _make_backend(b.type, b.options)
            return _instances[backend_name]

    return get_backend()


def available_backends() -> list[str]:
    return sorted(set(_REGISTRY.keys()) - {"elasticsearch", "opensearch"})


def clear_cache() -> None:
    """Discard all cached backend instances (useful in tests)."""
    _instances.clear()


__all__ = [
    "ObservabilityBackend",
    "get_backend",
    "get_backend_for_service",
    "available_backends",
    "clear_cache",
]

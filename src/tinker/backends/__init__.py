"""Observability backend registry.

The active backend is selected by the TINKER_BACKEND environment variable.
All backends implement the same ObservabilityBackend ABC — the agent and MCP
servers never import a specific backend class directly.

Supported values for TINKER_BACKEND
-------------------------------------
  cloudwatch   AWS CloudWatch Logs + Metrics + X-Ray
  gcp          GCP Cloud Logging + Cloud Monitoring + Cloud Trace
  azure        Azure Monitor Logs (KQL) + Azure Monitor Metrics + App Insights
  grafana      Loki (LogQL) + Prometheus (PromQL) + Tempo
  datadog      Datadog Logs API + Metrics API + APM
  elastic      Elasticsearch / OpenSearch
  otel         OpenSearch (logs) + Prometheus (metrics) — universal, provider-agnostic
"""

from __future__ import annotations

import structlog

from tinker.backends.base import ObservabilityBackend

log = structlog.get_logger(__name__)

# Lazy registry — values are strings to avoid importing all SDKs at startup.
# The backend class is only imported when actually requested.
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


def get_backend(name: str | None = None) -> ObservabilityBackend:
    """Return an initialised backend by name.

    If `name` is None the value of TINKER_BACKEND env var is used.
    """
    from tinker.config import settings

    key = (name or settings.tinker_backend).lower()
    if key not in _REGISTRY:
        available = ", ".join(_REGISTRY)
        raise ValueError(f"Unknown backend '{key}'. Available: {available}")

    module_path, cls_name = _REGISTRY[key].split(":")
    import importlib
    module = importlib.import_module(module_path)
    cls = getattr(module, cls_name)
    log.info("backend.init", backend=key)
    return cls()


def available_backends() -> list[str]:
    return sorted(set(_REGISTRY.keys()) - {"elasticsearch", "opensearch"})


__all__ = ["ObservabilityBackend", "get_backend", "available_backends"]

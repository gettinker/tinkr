"""Translate a Tinker QueryNode to a GCP Cloud Logging filter string.

resource_type controls the resource.type filter:
    "cloudrun"  → resource.type="cloud_run_revision"
    "gke"       → resource.type="k8s_container"
    "gce"       → resource.type="gce_instance"
    "cloudfn"   → resource.type="cloud_function"
    "appengine" → resource.type="gae_app"
    None        → resource.labels.service_name="{service}" (Cloud Run default)
"""

from __future__ import annotations

from tinker.query.ast import AndExpr, FieldFilter, NotExpr, OrExpr, QueryNode, TextFilter
from tinker.query.resource import GCP_RESOURCE

_SEVERITY_MAP: dict[str, str] = {
    "debug": "DEBUG",
    "info": "INFO",
    "warn": "WARNING",
    "warning": "WARNING",
    "error": "ERROR",
    "critical": "CRITICAL",
    "fatal": "CRITICAL",
}

# Cloud Logging severity is an ordered enum. When querying for "ERROR or above",
# use >= instead of listing every level individually.
# Order: DEBUG < INFO < NOTICE < WARNING < ERROR < CRITICAL < ALERT < EMERGENCY
_SEVERITY_RANGE: dict[str, str] = {
    "ERROR": 'severity >= "ERROR"',
    "CRITICAL": 'severity >= "CRITICAL"',
    "WARNING": 'severity >= "WARNING"',
}

_FIELD_MAP: dict[str, str] = {
    "level": "severity",
    "service": "resource.labels.service_name",
    "trace_id": "trace",
    "span_id": "spanId",
    # "message" handled separately — see _translate_field_filter
}


def _gcp_field(name: str) -> str:
    return _FIELD_MAP.get(name, name)


def _gcp_severity(v: str) -> str:
    return _SEVERITY_MAP.get(v.lower(), v.upper())


def _text_search(text: str) -> str:
    """Full-text search across both plain-text and structured JSON log payloads.

    SEARCH(text) is the correct Cloud Logging function for global text search:
    - Case-insensitive token matching
    - Covers textPayload, jsonPayload, and all other string fields
    - Uses an index — faster than the : substring operator
    See: https://cloud.google.com/logging/docs/view/logging-query-language#search-query
    """
    # Escape any double quotes inside the search text
    escaped = text.replace('"', '\\"')
    return f'SEARCH("{escaped}")'


def translate(node: QueryNode) -> str:
    """Return a GCP filter expression (without service/resource clause)."""
    if isinstance(node, TextFilter):
        if node.text == "*":
            return ""
        return _text_search(node.text)

    if isinstance(node, FieldFilter):
        return _translate_field_filter(node)

    if isinstance(node, AndExpr):
        l, r = translate(node.left), translate(node.right)
        if not l:
            return r
        if not r:
            return l
        return f"({l}) AND ({r})"

    if isinstance(node, OrExpr):
        l, r = translate(node.left), translate(node.right)
        return f"({l}) OR ({r})"

    if isinstance(node, NotExpr):
        inner = translate(node.operand)
        return f"NOT ({inner})"

    raise TypeError(f"Unknown node type: {type(node)}")


def _translate_field_filter(node: FieldFilter) -> str:
    """Translate a FieldFilter to a GCP native filter clause.

    Special cases:
    - level / severity → top-level `severity` field (not inside any payload)
    - message          → bare text search (matches textPayload and jsonPayload)
    - everything else  → mapped field with = operator
    """
    field = node.field

    if field == "level":
        # severity is a top-level indexed Cloud Logging field — NOT inside textPayload/jsonPayload.
        # Use >= range operator for single-level queries (e.g. ERROR means ERROR+CRITICAL+ALERT+EMERGENCY).
        # Use exact OR for explicit multi-level selections (e.g. level:(WARNING OR ERROR)).
        values = [_gcp_severity(v) for v in node.values]
        if len(values) == 1:
            mapped = _SEVERITY_RANGE.get(values[0])
            return mapped if mapped else f'severity="{values[0]}"'
        parts = [f'severity="{v}"' for v in values]
        return "(" + " OR ".join(parts) + ")"

    if field == "message":
        # Bare text search covers both textPayload and jsonPayload
        if len(node.values) == 1:
            return _text_search(node.values[0])
        parts = [_text_search(v) for v in node.values]
        return "(" + " OR ".join(parts) + ")"

    gcp_field = _gcp_field(field)
    if len(node.values) == 1:
        return f'{gcp_field}="{node.values[0]}"'
    parts = [f'{gcp_field}="{v}"' for v in node.values]
    return "(" + " OR ".join(parts) + ")"


def to_filter(node: QueryNode, service: str, resource_type: str | None = None) -> str:
    """Return a complete GCP Cloud Logging filter including resource and service."""
    if resource_type and resource_type.lower() in GCP_RESOURCE:
        rtype, label_key = GCP_RESOURCE[resource_type.lower()]
        resource_clause = f'resource.type="{rtype}" AND resource.labels.{label_key}="{service}"'
    elif resource_type:
        # Unknown type — best-effort pass-through
        resource_clause = (
            f'resource.labels.service_name="{service}" AND resource.type="{resource_type}"'
        )
    else:
        # Default: Cloud Run / generic service label
        resource_clause = f'resource.labels.service_name="{service}"'

    expr = translate(node)
    if not expr:
        return resource_clause
    return f"{resource_clause} AND ({expr})"

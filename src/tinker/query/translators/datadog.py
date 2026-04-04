"""Translate a Tinker QueryNode to a Datadog log search query string.

Datadog log search uses a Lucene-inspired syntax that is actually very close
to the Tinker query language itself, which makes this translator the simplest.

Examples:
    level:ERROR                     → @level:ERROR service:payments-api
    level:(ERROR OR WARN)           → @level:(ERROR OR WARN) service:payments-api
    "timeout" AND level:ERROR       → "timeout" @level:ERROR service:payments-api
    NOT "health check"              → -"health check" service:payments-api

Datadog quirks:
  - Custom attributes (not reserved) are prefixed with @: @level, @trace_id
  - Reserved attributes are not prefixed: service, host, status, env
  - NOT is expressed as a leading `-`
  - The `status` field maps to our `level`
"""

from __future__ import annotations

from tinker.query.ast import AndExpr, FieldFilter, NotExpr, OrExpr, QueryNode, TextFilter

# Datadog reserved (no @ prefix) vs custom (@ prefix)
_RESERVED = {"service", "host", "status", "env", "source", "tags"}

# Canonical Tinker field → Datadog field name (without prefix)
_FIELD_MAP: dict[str, str] = {
    "level":    "status",       # Datadog reserved attribute
    "service":  "service",      # reserved
    "message":  "message",      # reserved
    "trace_id": "trace_id",     # custom → @trace_id
    "span_id":  "span_id",      # custom → @span_id
}

_STATUS_MAP: dict[str, str] = {
    "debug":    "debug",
    "info":     "info",
    "warn":     "warn",
    "warning":  "warn",
    "error":    "error",
    "critical": "critical",
    "fatal":    "critical",
}


def _dd_field(name: str) -> str:
    mapped = _FIELD_MAP.get(name, name)
    return mapped if mapped in _RESERVED else f"@{mapped}"


def _dd_status(v: str) -> str:
    return _STATUS_MAP.get(v.lower(), v.lower())


def translate(node: QueryNode) -> str:
    """Return a Datadog log search expression (without the service clause)."""
    if isinstance(node, TextFilter):
        if node.text == "*":
            return ""
        return f'"{node.text}"'

    if isinstance(node, FieldFilter):
        field = _dd_field(node.field)
        values = (
            [_dd_status(v) for v in node.values]
            if node.field == "level"
            else node.values
        )
        if len(values) == 1:
            return f"{field}:{values[0]}"
        vals = " OR ".join(values)
        return f"{field}:({vals})"

    if isinstance(node, AndExpr):
        l, r = translate(node.left), translate(node.right)
        parts = [p for p in (l, r) if p]
        return " ".join(parts)  # Datadog implicit AND

    if isinstance(node, OrExpr):
        l, r = translate(node.left), translate(node.right)
        return f"({l} OR {r})"

    if isinstance(node, NotExpr):
        inner = translate(node.operand)
        # Wrap compound expressions
        if " " in inner and not inner.startswith('"'):
            return f"-({inner})"
        return f"-{inner}"

    raise TypeError(f"Unknown node type: {type(node)}")


def to_search_query(node: QueryNode, service: str) -> str:
    """Return a complete Datadog log search query string."""
    expr = translate(node)
    parts = [f"service:{service}"]
    if expr:
        parts.append(expr)
    return " ".join(parts)

"""Translate a Tinker QueryNode to an Elasticsearch/OpenSearch query DSL dict.

Returns a Python dict that can be passed directly as the `query` key in an
Elasticsearch search request body.

Examples:
    level:ERROR
      → {"bool": {"must": [
            {"term": {"log.level": "error"}},
            {"term": {"service.name": "payments-api"}}
        ]}}

    level:(ERROR OR WARN) AND "timeout"
      → {"bool": {"must": [
            {"terms": {"log.level": ["error", "warn"]}},
            {"match": {"message": "timeout"}},
            {"term": {"service.name": "payments-api"}}
        ]}}
"""

from __future__ import annotations

from typing import Any

from tinker.query.ast import AndExpr, FieldFilter, NotExpr, OrExpr, QueryNode, TextFilter
from tinker.query.resource import DEFAULT_ELASTIC_INDEX, ELASTIC_INDEX

_FIELD_MAP: dict[str, str] = {
    "level":    "log.level",
    "service":  "service.name",
    "message":  "message",
    "trace_id": "trace.id",
    "span_id":  "span.id",
}


def _es_field(name: str) -> str:
    return _FIELD_MAP.get(name, name)


def translate(node: QueryNode) -> dict[str, Any]:
    """Return an Elasticsearch query DSL dict for the given AST node."""
    if isinstance(node, TextFilter):
        if node.text == "*":
            return {"match_all": {}}
        return {"match": {"message": node.text}}

    if isinstance(node, FieldFilter):
        field = _es_field(node.field)
        values = [v.lower() for v in node.values] if node.field == "level" else node.values
        if len(values) == 1:
            return {"term": {field: values[0]}}
        return {"terms": {field: values}}

    if isinstance(node, AndExpr):
        left = translate(node.left)
        right = translate(node.right)
        # Flatten nested bool musts
        must: list[dict[str, Any]] = []
        for clause in (left, right):
            if "bool" in clause and "must" in clause["bool"] and len(clause["bool"]) == 1:
                must.extend(clause["bool"]["must"])
            else:
                must.append(clause)
        return {"bool": {"must": must}}

    if isinstance(node, OrExpr):
        return {"bool": {"should": [translate(node.left), translate(node.right)], "minimum_should_match": 1}}

    if isinstance(node, NotExpr):
        return {"bool": {"must_not": [translate(node.operand)]}}

    raise TypeError(f"Unknown node type: {type(node)}")


def resolve_index(resource_type: str | None) -> str:
    """Return the Elasticsearch index pattern for the given resource type."""
    if resource_type:
        return ELASTIC_INDEX.get(resource_type.lower(), DEFAULT_ELASTIC_INDEX)
    return DEFAULT_ELASTIC_INDEX


def to_query(node: QueryNode, service: str, resource_type: str | None = None) -> dict[str, Any]:
    """Return a complete Elasticsearch query dict with the service filter applied."""
    service_clause: dict[str, Any] = {"term": {"service.name": service}}
    expr = translate(node)

    if expr == {"match_all": {}}:
        return {"bool": {"must": [service_clause]}}

    if "bool" in expr and "must" in expr["bool"] and len(expr["bool"]) == 1:
        must_clauses: list[dict[str, Any]] = expr["bool"]["must"]
    else:
        must_clauses = [expr]

    return {"bool": {"must": [service_clause, *must_clauses]}}

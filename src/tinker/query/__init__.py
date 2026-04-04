"""Tinker unified query language.

Usage:
    from tinker.query import parse_query, translate_for

    ast = parse_query('level:ERROR AND "timeout"')

    # Get backend-specific query string / dict:
    logql       = translate_for("grafana",    ast, service="payments-api")
    insights    = translate_for("cloudwatch", ast, service="payments-api", resource_type="ecs")
    gcp_filter  = translate_for("gcp",        ast, service="my-service",   resource_type="cloudrun")
    kql         = translate_for("azure",      ast, service="my-app",       resource_type="aks")
    dd_query    = translate_for("datadog",    ast, service="payments-api")
    es_query    = translate_for("elastic",    ast, service="payments-api", resource_type="lambda")
"""

from __future__ import annotations

from typing import Any

from tinker.query.ast import QueryNode
from tinker.query.parser import parse


def parse_query(query: str) -> QueryNode:
    """Parse a Tinker unified query string and return an AST."""
    return parse(query)


def translate_for(
    backend: str,
    node: QueryNode,
    service: str,
    resource_type: str | None = None,
) -> Any:
    """Translate a parsed query AST to the native format for *backend*.

    Args:
        backend:       One of cloudwatch, grafana, gcp, azure, datadog, elastic.
        node:          Parsed query AST from parse_query().
        service:       Service / function / container name.
        resource_type: Infrastructure resource type (ecs, lambda, eks, rds, etc.).
                       Controls log group selection, KQL table, Loki labels, ES index.

    Returns:
        str  — for cloudwatch, grafana, gcp, azure, datadog
        dict — for elastic (Elasticsearch query DSL)
    """
    match backend.lower():
        case "cloudwatch":
            from tinker.query.translators.cloudwatch import to_insights_query
            return to_insights_query(node, service)

        case "grafana" | "loki":
            from tinker.query.translators.loki import translate as loki_translate
            return loki_translate(node, service, resource_type=resource_type)

        case "gcp":
            from tinker.query.translators.gcp import to_filter
            return to_filter(node, service, resource_type=resource_type)

        case "azure":
            from tinker.query.translators.azure import to_kql_where
            return to_kql_where(node, service, resource_type=resource_type)

        case "datadog":
            from tinker.query.translators.datadog import to_search_query
            return to_search_query(node, service)

        case "elastic" | "elasticsearch" | "opensearch" | "otel":
            from tinker.query.translators.elastic import to_query
            return to_query(node, service, resource_type=resource_type)

        case _:
            raise ValueError(f"Unknown backend: {backend!r}")


__all__ = ["parse_query", "translate_for", "QueryNode"]

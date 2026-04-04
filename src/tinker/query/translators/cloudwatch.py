"""Translate a Tinker QueryNode to a CloudWatch Logs Insights query string.

resource_type controls which log group is queried:
    "lambda"  → /aws/lambda/{service}
    "ecs"     → /ecs/{service}
    "eks"     → /aws/containerinsights/{service}/application
    "ec2"     → /aws/ec2/{service}
    "apigw"   → API-Gateway-Execution-Logs_{service}/prod
    "rds"     → /aws/rds/instance/{service}/postgresql
    None      → auto-discover via describe_log_groups
"""

from __future__ import annotations

import re

from tinker.query.ast import AndExpr, FieldFilter, NotExpr, OrExpr, QueryNode, TextFilter
from tinker.query.resource import CW_LOG_GROUP

# Map canonical Tinker field names → CloudWatch Insights field names
_FIELD_MAP: dict[str, str] = {
    "level":    "level",
    "service":  "service",
    "message":  "@message",
    "trace_id": "traceId",
    "span_id":  "spanId",
}


def _cw_field(name: str) -> str:
    return _FIELD_MAP.get(name, name)


def translate(node: QueryNode) -> str:
    """Return a CloudWatch Logs Insights filter expression (no leading `| filter`)."""
    if isinstance(node, TextFilter):
        if node.text == "*":
            return "1 = 1"
        escaped = node.text.replace("/", "\\/")
        if node.exact:
            return f"@message like /{re.escape(node.text)}/"
        return f"@message like /{escaped}/"

    if isinstance(node, FieldFilter):
        field = _cw_field(node.field)
        if len(node.values) == 1:
            return f"{field} = '{node.values[0]}'"
        vals = ", ".join(f"'{v}'" for v in node.values)
        return f"{field} in [{vals}]"

    if isinstance(node, AndExpr):
        l, r = translate(node.left), translate(node.right)
        if l == "1 = 1": return r
        if r == "1 = 1": return l
        return f"({l}) AND ({r})"

    if isinstance(node, OrExpr):
        return f"({translate(node.left)}) OR ({translate(node.right)})"

    if isinstance(node, NotExpr):
        return f"NOT ({translate(node.operand)})"

    raise TypeError(f"Unknown node type: {type(node)}")


def resolve_log_groups(resource_type: str | None, service: str) -> list[str]:
    """Return the list of log group name(s) to query for this service + resource type.

    Returns an empty list when auto-discovery should be used (caller should
    call describe_log_groups with the service name as pattern).
    """
    if resource_type is None:
        return []   # signal: auto-discover

    pattern = CW_LOG_GROUP.get(resource_type.lower())
    if pattern:
        return [pattern.format(service=service)]

    # Unknown resource type — treat as a literal log group prefix
    return [f"/{resource_type}/{service}"]


def to_insights_query(node: QueryNode, service: str) -> str:
    """Return a complete CloudWatch Logs Insights query string."""
    filter_expr = translate(node)
    combined = filter_expr if filter_expr != "1 = 1" else "1 = 1"
    return (
        "fields @timestamp, @message, level, service, traceId\n"
        f"| filter {combined}\n"
        "| sort @timestamp desc"
    )

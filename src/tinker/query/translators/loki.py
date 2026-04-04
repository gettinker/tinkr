"""Translate a Tinker QueryNode to a LogQL query string (Loki).

Strategy:
  - FieldFilter on `level` / `service` → stream selector labels  {level="ERROR"}
  - FieldFilter on other fields         → line filter + logfmt field match
  - TextFilter                          → |= "text" line filter
  - Boolean logic                       → multiple pipe stages (AND) or alternation
  - resource:TYPE                       → extra stream selector labels from LOKI_LABELS

LogQL doesn't have a native OR across streams, so we express OR as a union where
possible, falling back to a regexp line filter for text ORs.

Output examples:
  level:ERROR
    → {service="payments-api", level="ERROR"}

  level:ERROR AND "timeout"
    → {service="payments-api", level="ERROR"} |= `timeout`

  level:(ERROR OR WARN) AND "database"
    → {service="payments-api"} | level=~`ERROR|WARN` |= `database`

  resource:ecs AND level:ERROR
    → {service="payments-api", resource="container", level="ERROR"}

  NOT "health"
    → {service="payments-api"} != `health`
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tinker.query.ast import AndExpr, FieldFilter, NotExpr, OrExpr, QueryNode, TextFilter
from tinker.query.resource import LOKI_LABELS

# Fields that map to Loki stream selector labels
_LABEL_FIELDS = {"level", "service", "service_name", "app", "env", "namespace"}

# Canonical service label used in stream selectors — must match what the log
# shipper / dummy server actually sets on Loki streams.
_SERVICE_LABEL = "service"


@dataclass
class _LogQL:
    """Intermediate representation built while walking the AST."""
    labels: dict[str, str | list[str]] = field(default_factory=dict)   # exact label matches
    label_regexps: dict[str, str] = field(default_factory=dict)         # label =~ "regexp"
    line_filters: list[str] = field(default_factory=list)               # |= / != / |~
    logfmt_filters: list[str] = field(default_factory=list)             # | level="X"


def _collect(node: QueryNode, acc: _LogQL, negated: bool = False) -> None:
    """Walk the AST and populate _LogQL."""
    if isinstance(node, TextFilter):
        if node.text == "*":
            return
        op = "!=" if negated else "|="
        acc.line_filters.append(f'{op} `{node.text}`')

    elif isinstance(node, FieldFilter):
        fname = node.field
        if fname in _LABEL_FIELDS:
            if len(node.values) == 1:
                if negated:
                    acc.label_regexps[fname] = f"^(?!{node.values[0]}$).*"
                else:
                    acc.labels[fname] = node.values[0]
            else:
                pattern = "|".join(node.values)
                if negated:
                    acc.label_regexps[fname] = f"^(?!({pattern})$).*"
                else:
                    acc.label_regexps[fname] = pattern
        else:
            # Non-label field: use logfmt + field filter
            for v in node.values:
                op = "!=" if negated else "="
                acc.logfmt_filters.append(f'{fname}{op}"{v}"')

    elif isinstance(node, AndExpr):
        _collect(node.left, acc, negated)
        _collect(node.right, acc, negated)

    elif isinstance(node, OrExpr):
        # OR on text → regexp line filter
        # OR on same field is handled in FieldFilter with multi-values
        if isinstance(node.left, TextFilter) and isinstance(node.right, TextFilter):
            pattern = f"({node.left.text}|{node.right.text})"
            op = "!~" if negated else "|~"
            acc.line_filters.append(f'{op} `{pattern}`')
        else:
            # Best-effort: collect both sides (may over-match)
            _collect(node.left, acc, negated)
            _collect(node.right, acc, negated)

    elif isinstance(node, NotExpr):
        _collect(node.operand, acc, not negated)


def translate(node: QueryNode, service: str, resource_type: str | None = None) -> str:
    """Return a complete LogQL query string for the given service."""
    acc = _LogQL()
    _collect(node, acc)

    # Stream selector — start with service, add resource-specific labels
    stream: dict[str, str] = {_SERVICE_LABEL: service}
    if resource_type and resource_type.lower() in LOKI_LABELS:
        stream.update(LOKI_LABELS[resource_type.lower()])
    # Promote exact label matches into the stream selector
    for k, v in acc.labels.items():
        if k in ("service", "service_name"):
            continue  # service already set above
        stream[k] = v  # type: ignore[assignment]

    stream_str = ", ".join(f'{k}="{v}"' for k, v in stream.items())
    logql = "{" + stream_str + "}"

    # Label regexp matchers (after stream selector, before line filters)
    for k, pattern in acc.label_regexps.items():
        if k == "level" and k not in stream:
            logql += f' | level=~`{pattern}`'
        elif k not in stream:
            logql += f' | {k}=~`{pattern}`'

    # Logfmt field filters
    if acc.logfmt_filters:
        logql += " | logfmt | " + " | ".join(acc.logfmt_filters)

    # Line filters
    for lf in acc.line_filters:
        logql += f" {lf}"

    return logql

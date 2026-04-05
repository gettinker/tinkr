"""Query AST nodes for the Tinker unified query language.

Grammar (Lucene-lite):
    expr    = or_expr
    or_expr = and_expr ( 'OR' and_expr )*
    and_expr= unary ( 'AND'? unary )*      # AND is optional (implicit)
    unary   = 'NOT' unary | primary
    primary = '(' expr ')' | field_expr | text_expr
    field_expr = WORD ':' value            # level:ERROR, service:payments-api
    value   = WORD | '(' WORD ('OR' WORD)* ')'   # level:(ERROR OR WARN)
    text_expr  = QUOTED_STRING | WORD      # "connection timeout" | timeout
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union


# ── Node types ────────────────────────────────────────────────────────────────

@dataclass
class TextFilter:
    """Full-text substring match — e.g. `"timeout"` or `timeout`."""
    text: str
    exact: bool = False  # True when the original was a quoted string


@dataclass
class FieldFilter:
    """Field equality / multi-value — e.g. `level:ERROR` or `level:(ERROR OR WARN)`."""
    field: str
    values: list[str]   # always at least one element

    @property
    def single(self) -> str:
        return self.values[0]


@dataclass
class AndExpr:
    left: "QueryNode"
    right: "QueryNode"


@dataclass
class OrExpr:
    left: "QueryNode"
    right: "QueryNode"


@dataclass
class NotExpr:
    operand: "QueryNode"


QueryNode = Union[TextFilter, FieldFilter, AndExpr, OrExpr, NotExpr]


# ── Well-known field aliases ───────────────────────────────────────────────────
# Normalise user-facing names to the canonical ones used in translators.

FIELD_ALIASES: dict[str, str] = {
    "lvl": "level",
    "severity": "level",
    "svc": "service",
    "app": "service",
    "msg": "message",
    "trace": "trace_id",
    "span": "span_id",
}

# Fields whose values should always be lowercased for backend compatibility.
# e.g. Loki stores level="error", not level="ERROR".
_LOWERCASE_VALUE_FIELDS = {"level", "severity"}


def normalise_field(name: str) -> str:
    return FIELD_ALIASES.get(name.lower(), name.lower())


def normalise_value(field: str, value: str) -> str:
    """Normalise a field value.  Level/severity are always lowercased so that
    ``level:ERROR``, ``level:error``, and ``level:Error`` all resolve the same
    way in every backend.
    """
    if field in _LOWERCASE_VALUE_FIELDS:
        return value.lower()
    return value

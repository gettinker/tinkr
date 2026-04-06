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

def normalise_field(name: str) -> str:
    return FIELD_ALIASES.get(name.lower(), name.lower())


def normalise_value(field: str, value: str) -> str:
    """Pass the value through unchanged.

    Level casing is NOT normalised here because different log shippers use
    different conventions (e.g. level="ERROR" vs level="error"). Each backend
    translator is responsible for matching whatever the service actually emits.
    """
    return value

"""Recursive-descent parser for the Tinker unified query language.

Input examples:
    level:ERROR
    level:ERROR AND "timeout"
    level:(ERROR OR WARN) AND service:payments-api
    NOT "health check"
    level:ERROR AND NOT "test"
    "database connection" AND level:(ERROR OR CRITICAL)

Calling convention:
    from tinker.query.parser import parse
    ast = parse('level:ERROR AND "timeout"')
"""

from __future__ import annotations

import re
from typing import Iterator

from tinker.query.ast import (
    AndExpr,
    FieldFilter,
    NotExpr,
    OrExpr,
    QueryNode,
    TextFilter,
    normalise_field,
    normalise_value,
)

# ── Tokeniser ─────────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(
    r'"[^"]*"'          # quoted string
    r"|'[^']*'"         # single-quoted string
    r"|\bAND\b"
    r"|\bOR\b"
    r"|\bNOT\b"
    r"|[():]"           # parens and colon
    r"|[^\s():\"']+",   # bare word (includes field names and values)
    re.IGNORECASE,
)


def _tokenise(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.strip())


# ── Parser ────────────────────────────────────────────────────────────────────

class _Parser:
    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens
        self._pos = 0

    def _peek(self) -> str | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _consume(self) -> str:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def _match(self, *values: str) -> bool:
        tok = self._peek()
        return tok is not None and tok.upper() in {v.upper() for v in values}

    # ── Grammar rules ─────────────────────────────────────────────────────────

    def parse(self) -> QueryNode:
        node = self._or_expr()
        if self._peek() is not None:
            raise ValueError(f"Unexpected token at position {self._pos}: {self._peek()!r}")
        return node

    def _or_expr(self) -> QueryNode:
        left = self._and_expr()
        while self._match("OR"):
            self._consume()
            right = self._and_expr()
            left = OrExpr(left, right)
        return left

    def _and_expr(self) -> QueryNode:
        left = self._unary()
        while True:
            # Explicit AND or implicit (two primaries side-by-side)
            if self._match("AND"):
                self._consume()
            elif self._peek() in (None, ")", "OR"):
                break
            else:
                pass  # implicit AND — fall through to parse next primary
            right = self._unary()
            left = AndExpr(left, right)
        return left

    def _unary(self) -> QueryNode:
        if self._match("NOT"):
            self._consume()
            return NotExpr(self._unary())
        return self._primary()

    def _primary(self) -> QueryNode:
        tok = self._peek()
        if tok is None:
            raise ValueError("Unexpected end of query")

        # Sub-expression in parens
        if tok == "(":
            self._consume()
            node = self._or_expr()
            if self._peek() != ")":
                raise ValueError("Expected closing ')'")
            self._consume()
            return node

        # Quoted string — always a text filter
        if tok.startswith('"') or tok.startswith("'"):
            self._consume()
            return TextFilter(text=tok[1:-1], exact=True)

        # Look ahead: is this a field:value ?
        if self._pos + 1 < len(self._tokens) and self._tokens[self._pos + 1] == ":":
            field = normalise_field(self._consume())
            self._consume()  # ':'

            # value can be a multi-value group  level:(ERROR OR WARN)
            if self._peek() == "(":
                self._consume()
                values: list[str] = []
                while self._peek() != ")":
                    v = self._peek()
                    if v is None:
                        raise ValueError("Unclosed '(' in field value list")
                    if v.upper() == "OR":
                        self._consume()
                        continue
                    values.append(normalise_value(field, self._consume()))
                self._consume()  # ')'
                return FieldFilter(field=field, values=values)

            # Single value
            val = self._peek()
            if val is None:
                raise ValueError(f"Expected value after '{field}:'")
            if val.startswith('"') or val.startswith("'"):
                val = val[1:-1]
            self._consume()
            return FieldFilter(field=field, values=[normalise_value(field, val)])

        # Bare word — text filter
        word = self._consume()
        return TextFilter(text=word, exact=False)


# ── Public API ────────────────────────────────────────────────────────────────

def parse(query: str) -> QueryNode:
    """Parse a Tinker unified query string into an AST.

    Raises ValueError on syntax errors.
    Returns a TextFilter("*") for the wildcard query "*".
    """
    query = query.strip()
    if not query or query == "*":
        return TextFilter(text="*", exact=False)
    tokens = _tokenise(query)
    return _Parser(tokens).parse()

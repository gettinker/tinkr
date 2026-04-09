"""Classify anomalies by error type to drive investigation depth.

Classes:
  transient     — infrastructure / environmental: DB timeout, network error,
                  rate limit, bad input (4xx), connection refused, OOM
  config_error  — misconfiguration: missing env var, API key not set, invalid
                  config value, service failing health check due to missing dependency
  logic_bug     — code defects: NPE, AttributeError, wrong query, assertion error,
                  type error, unexpected business logic failure
  unknown       — insufficient signal to determine

The classification is a single cheap LLM call (~200 tokens).
It drives two decisions:
  1. explain — whether to fetch code context from GitHub
  2. fix     — minimal targeted fix vs full deep investigation
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class ErrorClass:
    kind: str  # "transient" | "config_error" | "logic_bug" | "unknown"
    confidence: float  # 0.0–1.0
    reason: str  # one-line explanation used in prompts
    has_stack_trace: bool
    stack_files: list[tuple[str, int]]  # [(file_path, line_number), ...]


# ── Heuristic patterns (fast path — no LLM cost) ─────────────────────────────

_TRANSIENT_PATTERNS = [
    r"timeout",
    r"timed out",
    r"connection refused",
    r"connection reset",
    r"connection pool",
    r"too many connections",
    r"rate limit",
    r"429",
    r"503",
    r"502",
    r"504",
    r"circuit breaker",
    r"retry",
    r"backoff",
    r"dns resolution",
    r"network unreachable",
    r"eof",
    r"broken pipe",
    r"bad request",
    r"invalid input",
    r"validation error",
    r"400",
    r"out of memory",
    r"oom",
    r"disk full",
    r"no space left",
    r"DataAccessResourceFailure",
    r"PoolInitializationException",
    r"HikariPool",
    r"hikari",
    r"JDBC Connection",
]

_CONFIG_ERROR_PATTERNS = [
    r"not configured",
    r"not set",
    r"missing.*key",
    r"API key.*not",
    r"api key.*missing",
    r"env.*not.*set",
    r"environment variable.*not",
    r"configuration.*missing",
    r"config.*invalid",
    r"invalid.*config",
    r"failed to load.*config",
    r"could not.*config",
    r'"status"\s*:\s*"degraded"',
    r'"issues"\s*:',
    r"health.*check.*fail",
    r"startup.*probe.*fail",
    r"readiness.*probe.*fail",
    r"liveness.*probe.*fail",
    r"service.*unavailable.*config",
]

_LOGIC_BUG_PATTERNS = [
    r"NullPointerException",
    r"AttributeError.*NoneType",
    r"TypeError.*None",
    r"undefined is not",
    r"cannot read propert",
    r"IndexError",
    r"KeyError",
    r"AssertionError",
    r"ZeroDivisionError",
    r"ArithmeticException",
    r"StackOverflowError",
    r"RecursionError",
    r"ClassCastException",
    r"IllegalArgumentException",
    r"logic error",
    r"assertion failed",
    r"unexpected state",
    r"wrong result",
    r"incorrect",
    r"invalid query",
]

_TRANSIENT_RE = re.compile("|".join(_TRANSIENT_PATTERNS), re.IGNORECASE)
_CONFIG_ERROR_RE = re.compile("|".join(_CONFIG_ERROR_PATTERNS), re.IGNORECASE)
_LOGIC_BUG_RE = re.compile("|".join(_LOGIC_BUG_PATTERNS), re.IGNORECASE)

# Stack trace line patterns — extract (file, line) pairs
_STACK_FILE_PATTERNS = [
    # Python:  File "src/payments/processor.py", line 142, in charge
    re.compile(r'File "([^"]+\.py)", line (\d+)'),
    # Java:    at com.tinker.orders.OrderService.simulateDbCall(OrderService.java:188)
    re.compile(r"at [\w.$]+\((\w+\.java):(\d+)\)"),
    # Go:      /app/main.go:142 +0x1a8
    re.compile(r"(/[^\s]+\.go):(\d+)"),
    # Node.js: at processPayment (/app/src/payments.js:142:15)
    re.compile(r"at \S+ \((/[^\s]+\.js):(\d+):\d+\)"),
    # Node.js (no function name): at /app/src/payments.js:142:15
    re.compile(r"at (/[^\s]+\.js):(\d+):\d+"),
]

# Paths that are framework/stdlib — not worth fetching
_SKIP_PATH_PATTERNS = re.compile(
    r"node_modules|site-packages|jdk|java\.base|sun\.|com\.sun\.|"
    r"org\.springframework|org\.apache|org\.hibernate|ch\.qos|"
    r"net\.logstash|io\.netty|reactor\.|kotlinx\.|scala\.",
    re.IGNORECASE,
)


def _extract_text(anomaly: dict[str, Any]) -> str:
    """Pull all text fields from an anomaly dict into one string for pattern matching.

    Extracts actual message content rather than stringifying dicts, so patterns
    can match against real log content (e.g. JSON health check response bodies,
    missing config key messages).
    """
    parts: list[str] = []
    for key in ("description", "metric", "message"):
        if v := anomaly.get(key):
            parts.append(str(v))
    log_summary = anomaly.get("log_summary") or {}
    for pattern in log_summary.get("unique_patterns") or []:
        if isinstance(pattern, dict):
            # Pull actual message content, not the dict repr
            for field in ("template", "example", "message"):
                if v := pattern.get(field):
                    parts.append(str(v))
            for entry in pattern.get("sample_entries") or []:
                if v := entry.get("message"):
                    parts.append(str(v))
        else:
            parts.append(str(pattern))
    for trace in log_summary.get("stack_traces") or []:
        if isinstance(trace, dict):
            for field in ("signature", "full_trace"):
                if v := trace.get(field):
                    parts.append(str(v))
        else:
            parts.append(str(trace))
    return "\n".join(parts)


def _extract_stack_files(text: str) -> list[tuple[str, int]]:
    """Return unique (file_path, line_number) pairs from stack trace text."""
    seen: set[str] = set()
    results: list[tuple[str, int]] = []
    for pattern in _STACK_FILE_PATTERNS:
        for m in pattern.finditer(text):
            path, lineno = m.group(1), int(m.group(2))
            # Skip framework paths
            if _SKIP_PATH_PATTERNS.search(path):
                continue
            # Normalise — strip leading /app/ or similar docker paths
            norm = re.sub(r"^/(?:app|src|home/\w+/\w+)/", "", path)
            key = f"{norm}:{lineno}"
            if key not in seen:
                seen.add(key)
                results.append((norm, lineno))
    return results[:5]  # cap at 5 files to avoid excessive API calls


def classify(anomaly: dict[str, Any]) -> ErrorClass:
    """Classify an anomaly using heuristic patterns. No LLM call needed."""
    text = _extract_text(anomaly)
    stack_files = _extract_stack_files(text)
    has_stack = bool(stack_files) or bool(
        re.search(r"Traceback|at com\.|at org\.|goroutine \d+", text)
    )

    config_hit = bool(_CONFIG_ERROR_RE.search(text))
    transient_hit = bool(_TRANSIENT_RE.search(text))
    logic_hit = bool(_LOGIC_BUG_RE.search(text))

    # config_error takes highest priority — a missing API key or failed health check
    # due to misconfiguration should never be classified as transient even if the
    # log also contains "503 Service Unavailable".
    if config_hit:
        return ErrorClass(
            kind="config_error",
            confidence=0.9,
            reason="Log contains missing config key, env var not set, or degraded health check response",
            has_stack_trace=has_stack,
            stack_files=stack_files,
        )
    if logic_hit and not transient_hit:
        return ErrorClass(
            kind="logic_bug",
            confidence=0.85,
            reason="Stack trace contains a code defect (NPE, AttributeError, assertion, etc.)",
            has_stack_trace=has_stack,
            stack_files=stack_files,
        )
    if transient_hit and not logic_hit:
        return ErrorClass(
            kind="transient",
            confidence=0.85,
            reason="Error matches infrastructure/transient pattern (timeout, connection, rate limit)",
            has_stack_trace=has_stack,
            stack_files=stack_files,
        )
    if transient_hit and logic_hit:
        # Both match — logic_bug takes precedence (more actionable)
        return ErrorClass(
            kind="logic_bug",
            confidence=0.6,
            reason="Mixed signals — code defect pattern detected alongside transient pattern",
            has_stack_trace=has_stack,
            stack_files=stack_files,
        )

    # No strong signal — fall back to LLM
    return _classify_with_llm(text, has_stack, stack_files)


def _classify_with_llm(
    text: str,
    has_stack: bool,
    stack_files: list[tuple[str, int]],
) -> ErrorClass:
    """Single cheap LLM call to classify when heuristics are inconclusive."""
    try:
        from tinker.agent import llm as llm_mod
        from tinker import toml_config as tc

        prompt = (
            "Classify this production anomaly as ONE of: transient, config_error, logic_bug, unknown.\n\n"
            "config_error = misconfiguration: missing env var, API key not set, "
            "service failing health check because a required config value is absent or wrong.\n"
            "transient = infrastructure/environment issue: DB timeout, network error, "
            "rate limit, connection refused, OOM. Not caused by missing config.\n"
            "logic_bug = code defect: null pointer, wrong query, assertion failure, "
            "type error, incorrect business logic.\n"
            "unknown = cannot determine.\n\n"
            "IMPORTANT: If the log shows a health check failing because an API key or env var "
            "is not configured, classify as config_error even if the HTTP status is 503.\n\n"
            "Respond with exactly: <class>|<one sentence reason>\n"
            "Example: config_error|OPENROUTER_API_KEY is not set, causing the health check to return degraded.\n\n"
            f"Anomaly:\n{text[:1500]}"
        )
        response = llm_mod.complete(
            [{"role": "user", "content": prompt}],
            model=tc.get().llm.default_model,
            max_tokens=100,
        )
        raw = llm_mod.extract_text(response).strip()
        parts = raw.split("|", 1)
        kind = parts[0].strip().lower()
        reason = parts[1].strip() if len(parts) > 1 else raw
        if kind not in ("transient", "logic_bug", "unknown"):
            kind = "unknown"
        return ErrorClass(
            kind=kind,
            confidence=0.7,
            reason=reason,
            has_stack_trace=has_stack,
            stack_files=stack_files,
        )
    except Exception:
        return ErrorClass(
            kind="unknown",
            confidence=0.0,
            reason="Classification failed — using deep investigation mode",
            has_stack_trace=has_stack,
            stack_files=stack_files,
        )

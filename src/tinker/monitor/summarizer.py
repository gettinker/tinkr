"""Log deduplication and context builder for cost-efficient LLM explain/fix calls.

Pipeline
--------
1. Template extraction  — strip variable parts (IPs, numbers, UUIDs, timestamps)
                          so "timeout to 192.168.1.3:5432 after 30s" and
                          "timeout to 10.0.0.7:5432 after 45s" collapse to one pattern.

2. Stack trace detection — find multi-line exception traces in log messages
                           (Python, Java, Node, Go, Ruby).  Deduplicate by
                           exception-type + first frame signature.

3. Context assembly     — build a compact dict (~300-500 tokens) containing:
                            total_count, unique_patterns (top-10 with counts),
                            stack_traces (top-5 deduplicated), time_distribution,
                            common_fields.

4. Representative logs  — pick one example per unique error pattern, preferring
                          entries that contain a stack trace.

MAX_EXPLAIN_TOKENS ≈ 2000 regardless of how many raw errors occurred.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tinker.backends.base import LogEntry


# ── Variable-part normalisation patterns ──────────────────────────────────────
# Order matters: more specific patterns first.

_VAR_SUBS: list[tuple[re.Pattern, str]] = [
    # UUIDs
    (re.compile(
        r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b',
        re.I,
    ), '<uuid>'),
    # ISO timestamps (keep near top — must beat plain number)
    (re.compile(
        r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?'
    ), '<ts>'),
    # Unix epoch timestamps (10–13 digits)
    (re.compile(r'\b\d{10,13}\b'), '<ts>'),
    # Hex strings ≥ 12 chars (commit shas, trace IDs, etc.)
    (re.compile(r'\b[0-9a-f]{12,}\b'), '<hex>'),
    # IPv4 with optional port
    (re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?\b'), '<ip>'),
    # File-system paths (≥ 2 components)
    (re.compile(r'(?:/[a-zA-Z0-9_.\-]+){2,}'), '<path>'),
    # Long quoted strings (> 12 chars)
    (re.compile(r'"[^"\n]{12,}"'), '"<str>"'),
    # Remaining standalone numbers
    (re.compile(r'\b\d+(?:\.\d+)?\b'), '<n>'),
]

# ── Stack trace header patterns ───────────────────────────────────────────────
# These identify the *start* of a stack trace within a (possibly multi-line) message.

_TRACE_HEADERS: list[tuple[str, re.Pattern]] = [
    ("python",  re.compile(r'Traceback \(most recent call last\)', re.M)),
    ("python",  re.compile(
        r'(?:ValueError|TypeError|KeyError|AttributeError|RuntimeError|'
        r'ImportError|OSError|IOError|IndexError|NameError|'
        r'NotImplementedError|StopIteration|AssertionError|'
        r'FileNotFoundError|PermissionError|ConnectionError|'
        r'TimeoutError|MemoryError|OverflowError|ZeroDivisionError)'
        r': .', re.M,
    )),
    ("java",    re.compile(r'(?:Exception|Error) in thread\b', re.M)),
    ("java",    re.compile(r'\b(?:\w+\.)+(?:\w+Exception|\w+Error):', re.M)),
    ("java",    re.compile(r'Caused by:', re.M)),
    ("node",    re.compile(r'\bError: .+\n\s+at ', re.M | re.S)),
    ("go",      re.compile(r'goroutine \d+ \[', re.M)),
    ("go",      re.compile(r'\bpanic:', re.M)),
    ("ruby",    re.compile(r'\((?:\w+::)?\w+Error\)', re.M)),
]

# Lines that look like stack frames (for boundary detection)
_FRAME_LINE = re.compile(
    r'(?:'
    r'^\s+File "[^"]+", line \d+'              # Python
    r'|^\s+at [\w.$<>]+\('                     # Java / Node
    r'|^\t[\w./]+\.go:\d+'                     # Go source line
    r'|^\s+\S+\+0x[0-9a-f]+'                  # Go symbol
    r'|^\s+from .+:\d+:in '                    # Ruby
    r')',
    re.M,
)


def _normalize_message(msg: str) -> str:
    """Replace variable parts in *msg* with placeholders."""
    for pattern, replacement in _VAR_SUBS:
        msg = pattern.sub(replacement, msg)
    # Collapse runs of whitespace to a single space
    return re.sub(r'\s+', ' ', msg).strip()


def _expand_escaped_newlines(msg: str) -> str:
    """Some log shippers store multi-line messages as JSON with literal \\n."""
    return msg.replace('\\n', '\n').replace('\\t', '\t')


def _detect_stack_trace(message: str) -> tuple[str | None, str | None]:
    """Return (language, trace_text) if *message* contains a stack trace, else (None, None).

    Works on both literal newlines and escaped ``\\n`` in the message.
    """
    expanded = _expand_escaped_newlines(message)
    for lang, pattern in _TRACE_HEADERS:
        if pattern.search(expanded):
            return lang, expanded
    return None, None


def _trace_signature(trace_text: str) -> str:
    """Return a short deduplication key for a stack trace.

    Key = normalised exception/error line + first stack frame line.
    Variable parts (IPs, numbers, paths) are stripped so two stack traces
    that differ only in the value of a connection target collapse to one.
    """
    lines = trace_text.splitlines()
    exc_line = ""
    frame_line = ""
    for line in lines:
        stripped = line.strip()
        if not exc_line and re.search(
            r'(?:Error|Exception|panic)[\w.]*:', stripped
        ):
            exc_line = _normalize_message(stripped)[:120]
        if not frame_line and _FRAME_LINE.match(line):
            frame_line = stripped[:80]
        if exc_line and frame_line:
            break
    return f"{exc_line} | {frame_line}".strip(" |")


def _peak_minute(timestamps: list[datetime]) -> str | None:
    if not timestamps:
        return None
    counts: Counter[str] = Counter()
    for ts in timestamps:
        counts[ts.strftime("%Y-%m-%dT%H:%M")] += 1
    return max(counts, key=counts.__getitem__) + ":00Z"


# ── Public API ────────────────────────────────────────────────────────────────

class LogSummarizer:
    """Compress an arbitrary-length list of LogEntry objects into a compact
    dict + a small representative sample, suitable for LLM explain/fix calls.
    """

    MAX_PATTERNS = 10
    MAX_STACK_TRACES = 5
    MAX_REPRESENTATIVE = 10

    def summarize(
        self,
        logs: list[LogEntry],
        window_minutes: int = 10,
    ) -> tuple[list[LogEntry], dict]:
        """Return ``(representative_logs, summary_dict)``.

        *representative_logs* — one example per unique error pattern, preferring
        entries that contain a stack trace.  Capped at MAX_REPRESENTATIVE.

        *summary_dict* — compact structure consumed by LLM context builder.
        """
        if not logs:
            return [], {"total_count": 0, "window_minutes": window_minutes}

        # ── 1. Classify each log: template + stack trace ──────────────────────
        pattern_counts: Counter[str] = Counter()
        pattern_example: dict[str, LogEntry] = {}
        trace_by_sig: dict[str, dict] = {}
        timestamps: list[datetime] = []
        field_counter: Counter[tuple[str, str]] = Counter()

        for entry in logs:
            msg = entry.message or ""
            tmpl = _normalize_message(msg)
            pattern_counts[tmpl] += 1
            timestamps.append(entry.timestamp)

            # Prefer stack-trace-bearing entries as the representative example
            lang, trace_text = _detect_stack_trace(msg)
            if tmpl not in pattern_example or (lang and not _has_trace(pattern_example[tmpl])):
                pattern_example[tmpl] = entry

            # Deduplicate stack traces by signature
            if trace_text:
                sig = _trace_signature(trace_text)
                if sig not in trace_by_sig:
                    trace_by_sig[sig] = {
                        "language": lang,
                        "signature": sig,
                        "full_trace": _trim_trace(trace_text),
                        "count": 0,
                    }
                trace_by_sig[sig]["count"] += 1

            # Common fields: count field=value pairs across entries
            for k, v in (entry.extra or {}).items():
                if k and v and len(str(v)) < 50:
                    field_counter[(k, str(v))] += 1

        # ── 2. Top patterns ───────────────────────────────────────────────────
        top_patterns = pattern_counts.most_common(self.MAX_PATTERNS)

        unique_patterns = [
            {
                "template": tmpl,
                "count": cnt,
                "example": (pattern_example[tmpl].message or "")[:200],
            }
            for tmpl, cnt in top_patterns
        ]

        # ── 3. Top stack traces ───────────────────────────────────────────────
        stack_traces = sorted(
            trace_by_sig.values(), key=lambda t: t["count"], reverse=True
        )[: self.MAX_STACK_TRACES]

        # ── 4. Time distribution ──────────────────────────────────────────────
        sorted_ts = sorted(timestamps)
        time_distribution = {
            "first_seen": sorted_ts[0].isoformat() if sorted_ts else None,
            "last_seen": sorted_ts[-1].isoformat() if sorted_ts else None,
            "peak_minute": _peak_minute(sorted_ts),
        }

        # ── 5. Common fields (present in > 60 % of entries) ──────────────────
        threshold = max(2, int(len(logs) * 0.6))
        common_fields = {
            k: v
            for (k, v), cnt in field_counter.items()
            if cnt >= threshold
        }

        summary = {
            "total_count": len(logs),
            "unique_pattern_count": len(pattern_counts),
            "window_minutes": window_minutes,
            "unique_patterns": unique_patterns,
            "stack_traces": stack_traces,
            "time_distribution": time_distribution,
            "common_fields": common_fields,
        }

        # ── 6. Representative logs (one per top pattern, trace-first) ─────────
        representative: list[LogEntry] = []
        for tmpl, _ in top_patterns[: self.MAX_REPRESENTATIVE]:
            if tmpl in pattern_example:
                representative.append(pattern_example[tmpl])

        return representative, summary


# ── Helpers ───────────────────────────────────────────────────────────────────

def _has_trace(entry: LogEntry) -> bool:
    lang, _ = _detect_stack_trace(entry.message or "")
    return lang is not None


def _trim_trace(trace: str, max_lines: int = 30) -> str:
    """Keep the first *max_lines* lines of a stack trace."""
    lines = trace.splitlines()
    trimmed = lines[:max_lines]
    if len(lines) > max_lines:
        trimmed.append(f"... ({len(lines) - max_lines} more lines)")
    return "\n".join(trimmed)


def build_explain_context(anomaly_dict: dict) -> str:
    """Render a compact text block for the LLM explain prompt.

    Converts the anomaly dict (from ``Anomaly.to_dict()``) into a structured
    prompt section that stays well under MAX_EXPLAIN_TOKENS.
    """
    lines: list[str] = []
    lines.append(f"Service: {anomaly_dict.get('service', '?')}")
    lines.append(f"Metric:  {anomaly_dict.get('metric', '?')}")
    lines.append(f"Severity: {anomaly_dict.get('severity', '?').upper()}")
    lines.append(f"Description: {anomaly_dict.get('description', '?')}")
    lines.append(
        f"Value: {anomaly_dict.get('current_value', '?')} "
        f"(threshold: {anomaly_dict.get('threshold', '?')})"
    )
    lines.append(f"Detected: {anomaly_dict.get('detected_at', '?')}")

    summary = anomaly_dict.get("log_summary") or {}
    if summary:
        lines.append("")
        lines.append(
            f"Log summary: {summary.get('total_count', '?')} total entries, "
            f"{summary.get('unique_pattern_count', '?')} unique patterns "
            f"over {summary.get('window_minutes', '?')}m"
        )

        td = summary.get("time_distribution") or {}
        if td.get("first_seen"):
            lines.append(
                f"  first={td['first_seen']}  last={td.get('last_seen','?')}"
                f"  peak={td.get('peak_minute','?')}"
            )

        cf = summary.get("common_fields") or {}
        if cf:
            lines.append(
                "  Common fields: "
                + ", ".join(f"{k}={v}" for k, v in list(cf.items())[:5])
            )

        for i, p in enumerate(summary.get("unique_patterns") or [], 1):
            lines.append(f"  Pattern {i} ({p['count']}×): {p['template'][:120]}")
            lines.append(f"    Example: {p['example'][:160]}")

        for t in summary.get("stack_traces") or []:
            lines.append("")
            lines.append(
                f"Stack trace [{t.get('language','?')}]"
                f" — {t.get('count', 0)}× occurrences"
            )
            lines.append(f"  Signature: {t.get('signature','?')[:120]}")
            # First 10 lines of the full trace
            trace_preview = "\n".join(
                (t.get("full_trace") or "").splitlines()[:10]
            )
            if trace_preview:
                lines.append("  " + trace_preview.replace("\n", "\n  "))

    return "\n".join(lines)

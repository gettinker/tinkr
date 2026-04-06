"""Output renderers for CLI commands.

Three formats controlled by ``OutputFormat``:
  table      — Rich formatted table (default, human-readable)
  json       — single JSON array / object on stdout
  jsonlines  — one JSON object per line (streaming-friendly, pipeable to jq)

Usage
-----
    from tinker.interfaces.renderers import OutputFormat, render_anomalies

    render_anomalies(anomalies, OutputFormat.table, service="payments-api", since="1h")
    render_anomalies(anomalies, OutputFormat.jsonlines)

Adding a new render target
--------------------------
1. Add a ``render_<thing>(items, fmt, **ctx)`` function here.
2. Implement all three format branches (json / jsonlines / table).
3. Call it from the CLI command and the Slack command formatter.
"""

from __future__ import annotations

import json as _json
from enum import Enum
from typing import Any

from rich.console import Console
from rich.table import Table

from tinker.backends.base import Anomaly, LogEntry, MetricPoint

console = Console()

SEVERITY_COLORS: dict[str, str] = {
    "critical": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "green",
    "unknown": "white",
}

_LEVEL_STYLES: dict[str, str] = {
    "ERROR": "red",
    "CRITICAL": "bold red",
    "WARN": "yellow",
    "WARNING": "yellow",
    "INFO": "green",
    "DEBUG": "dim",
}


class OutputFormat(str, Enum):
    table = "table"
    json = "json"
    jsonlines = "jsonlines"


# ── Serialisers (shared between json + jsonlines) ─────────────────────────────

def _log_dict(e: LogEntry) -> dict[str, Any]:
    return {
        "timestamp": e.timestamp.isoformat(),
        "level": e.level,
        "service": e.service,
        "message": e.message,
        "trace_id": e.trace_id,
    }


def _metric_dict(p: MetricPoint) -> dict[str, Any]:
    return {
        "timestamp": p.timestamp.isoformat(),
        "value": p.value,
        "unit": p.unit,
    }


# ── Logs ──────────────────────────────────────────────────────────────────────

def render_logs(entries: list[LogEntry], fmt: OutputFormat) -> None:
    if fmt == OutputFormat.json:
        print(_json.dumps([_log_dict(e) for e in entries], default=str))
        return
    if fmt == OutputFormat.jsonlines:
        for e in entries:
            print(_json.dumps(_log_dict(e), default=str))
        return
    # table
    if not entries:
        console.print("[dim]No log entries found.[/dim]")
        return
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Timestamp", style="dim", width=20)
    table.add_column("Level", width=8)
    table.add_column("Message")
    for e in entries:
        style = _LEVEL_STYLES.get(e.level.upper(), "white")
        table.add_row(
            e.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            f"[{style}]{e.level}[/{style}]",
            e.message[:200],
        )
    console.print(table)


def render_log_entry(e: LogEntry, fmt: OutputFormat) -> None:
    """Render a single log entry — used for streaming tail."""
    if fmt in (OutputFormat.json, OutputFormat.jsonlines):
        print(_json.dumps(_log_dict(e), default=str))
        return
    style = _LEVEL_STYLES.get(e.level.upper(), "white")
    ts = e.timestamp.strftime("%H:%M:%S")
    console.print(f"[dim]{ts}[/dim]  [{style}]{e.level:<8}[/{style}]  {e.message[:200]}")


# ── Metrics ───────────────────────────────────────────────────────────────────

def render_metrics(points: list[MetricPoint], fmt: OutputFormat) -> None:
    if fmt == OutputFormat.json:
        print(_json.dumps([_metric_dict(p) for p in points], default=str))
        return
    if fmt == OutputFormat.jsonlines:
        for p in points:
            print(_json.dumps(_metric_dict(p), default=str))
        return
    if not points:
        console.print("[dim]No metric data found.[/dim]")
        return
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Timestamp", style="dim")
    table.add_column("Value", justify="right")
    for p in points[-20:]:
        table.add_row(p.timestamp.strftime("%H:%M:%S"), f"{p.value:.4g}")
    console.print(table)


# ── Anomalies ─────────────────────────────────────────────────────────────────

def render_anomalies(
    anomalies: list[Anomaly],
    fmt: OutputFormat,
    service: str = "",
    since: str = "",
) -> None:
    if fmt == OutputFormat.json:
        print(_json.dumps([a.to_dict() for a in anomalies], indent=2, default=str))
        return
    if fmt == OutputFormat.jsonlines:
        for a in anomalies:
            print(_json.dumps(a.to_dict(), default=str))
        return
    if not anomalies:
        console.print(f"[dim]No anomalies detected for {service} in the last {since}.[/dim]")
        return
    table = Table(
        show_header=True,
        header_style="bold magenta",
        title=f"Anomalies — {service} (last {since})" if service else "Anomalies",
    )
    table.add_column("#", width=3, justify="right")
    table.add_column("Severity", width=9)
    table.add_column("Metric", width=20)
    table.add_column("Description")
    table.add_column("Patterns", width=8, justify="right")
    table.add_column("Traces", width=7, justify="right")
    for i, a in enumerate(anomalies, 1):
        sev_style = SEVERITY_COLORS.get(a.severity.lower(), "white")
        n_patterns = len((a.log_summary or {}).get("unique_patterns") or [])
        n_traces = len((a.log_summary or {}).get("stack_traces") or [])
        table.add_row(
            str(i),
            f"[{sev_style}]{a.severity.upper()}[/{sev_style}]",
            a.metric,
            a.description[:80],
            str(n_patterns) if n_patterns else "—",
            str(n_traces) if n_traces else "—",
        )
    console.print(table)
    if service:
        console.print(
            f"[dim]Run [bold]tinker monitor {service}[/bold] to explain and fix anomalies.[/dim]"
        )


# ── Watches ───────────────────────────────────────────────────────────────────

def render_watches(watches: list[dict], fmt: OutputFormat) -> None:
    if fmt == OutputFormat.json:
        print(_json.dumps(watches, indent=2, default=str))
        return
    if fmt == OutputFormat.jsonlines:
        for w in watches:
            print(_json.dumps(w, default=str))
        return
    if not watches:
        console.print("[dim]No watches on the server.[/dim]")
        return
    table = Table(show_header=True, header_style="bold magenta", title="Server Watches")
    table.add_column("ID", width=16)
    table.add_column("Service", width=20)
    table.add_column("Status", width=9)
    table.add_column("Notifier", width=12)
    table.add_column("Destination", width=16)
    table.add_column("Interval", width=10)
    table.add_column("Last Run")
    for w in watches:
        status = w.get("status", "?")
        scolor = "green" if status == "running" else "dim"
        table.add_row(
            w.get("watch_id", "?"),
            w.get("service", "?"),
            f"[{scolor}]{status}[/{scolor}]",
            w.get("notifier") or "—",
            w.get("destination") or "—",
            f"{w.get('interval_seconds', '?')}s",
            (w.get("last_run_at") or "never")[:19],
        )
    console.print(table)

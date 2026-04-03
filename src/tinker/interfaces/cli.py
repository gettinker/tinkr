"""Tinker CLI — built with Typer + Rich."""

from __future__ import annotations

import asyncio
from typing import Optional

import structlog
import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from tinker import __version__

app = typer.Typer(
    name="tinker",
    help="AI-powered observability and incident response agent.",
    add_completion=False,
)
console = Console()
log = structlog.get_logger(__name__)

SEVERITY_COLORS = {
    "critical": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "green",
    "unknown": "white",
}


# ── Commands ──────────────────────────────────────────────────────────────────


@app.command()
def version() -> None:
    """Print the Tinker version."""
    console.print(f"tinker {__version__}")


@app.command()
def analyze(
    service: str = typer.Argument(..., help="Service or application name to analyze"),
    since: str = typer.Option("1h", "--since", "-s", help="Time window: 1h, 30m, 2d"),
    backend: str = typer.Option("cloudwatch", "--backend", "-b", help="cloudwatch|elastic|gcp"),
    deep: bool = typer.Option(False, "--deep", help="Use claude-opus with extended thinking"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Stream agent thinking"),
) -> None:
    """Analyze a service for incidents and produce a root cause report."""
    asyncio.run(_analyze(service, since, backend, deep, verbose))


@app.command()
def fix(
    incident_id: str = typer.Argument(..., help="Incident ID from a previous analyze run"),
    approve: bool = typer.Option(
        False, "--approve", help="Apply the fix and open a PR (requires explicit flag)"
    ),
) -> None:
    """Display suggested fix for an incident, or apply it with --approve."""
    asyncio.run(_fix(incident_id, approve))


@app.command()
def monitor(
    services: str = typer.Option(..., "--services", help="Comma-separated service names"),
    backend: str = typer.Option("cloudwatch", "--backend", "-b"),
    interval: int = typer.Option(60, "--interval", "-i", help="Poll interval in seconds"),
) -> None:
    """Start the continuous monitoring loop and print anomalies to stdout."""
    service_list = [s.strip() for s in services.split(",")]
    asyncio.run(_monitor(service_list, backend, interval))


@app.command()
def logs(
    service: str = typer.Argument(...),
    query: str = typer.Option("*", "--query", "-q", help="Log query string"),
    since: str = typer.Option("30m", "--since", "-s"),
    backend: str = typer.Option("cloudwatch", "--backend", "-b"),
    limit: int = typer.Option(50, "--limit", "-n"),
) -> None:
    """Tail logs for a service (raw, no AI analysis)."""
    asyncio.run(_logs(service, query, since, backend, limit))


# ── Async implementations ─────────────────────────────────────────────────────


async def _analyze(
    service: str, since: str, backend_name: str, deep: bool, verbose: bool
) -> None:
    from tinker.agent.orchestrator import AgentSession, Orchestrator
    from tinker.backends import get_backend

    backend = get_backend(backend_name)
    orch = Orchestrator(use_deep_rca=deep)
    session = AgentSession(service=service)

    console.print(
        Panel(f"[bold]Analyzing[/bold] [cyan]{service}[/cyan] (last {since})", expand=False)
    )

    if verbose:
        console.print("[dim]Streaming agent thoughts...[/dim]\n")
        async for chunk in orch.stream_analyze(service, since, session):
            console.print(chunk, end="")
        console.print()
    else:
        with console.status(f"[bold green]Running RCA on {service}...[/bold green]"):
            report = await orch.analyze(service, since, session)

        _print_report(report)


async def _fix(incident_id: str, approve: bool) -> None:
    # In a real implementation, sessions would be persisted to disk/Redis
    console.print(f"[yellow]Looking up fix for[/yellow] {incident_id}...")
    if approve:
        confirmed = typer.confirm(
            f"Apply fix for {incident_id} and open a PR? This cannot be undone.",
            default=False,
        )
        if not confirmed:
            console.print("[red]Aborted.[/red]")
            raise typer.Exit(1)
        console.print("[green]Applying fix...[/green]")
        # TODO: load persisted session and call dispatcher.apply_fix
    else:
        console.print("[dim]Re-run with --approve to apply the fix.[/dim]")


async def _monitor(services: list[str], backend_name: str, interval: int) -> None:
    from tinker.backends import get_backend
    from tinker.monitor.loop import MonitoringLoop

    backend = get_backend(backend_name)
    loop = MonitoringLoop(backend=backend, services=services, poll_interval=interval)

    async def print_anomaly(anomaly: object) -> None:
        from tinker.backends.base import Anomaly
        assert isinstance(anomaly, Anomaly)
        color = SEVERITY_COLORS.get(anomaly.severity, "white")
        console.print(
            Panel(
                f"[{color}][{anomaly.severity.upper()}][/{color}] "
                f"[bold]{anomaly.service}[/bold] — {anomaly.description}",
                title="Anomaly Detected",
                border_style=color,
            )
        )

    loop.add_alert_handler(print_anomaly)
    console.print(f"[bold green]Monitoring[/bold green] {', '.join(services)} "
                  f"(interval: {interval}s). Press Ctrl+C to stop.\n")
    try:
        await loop.run()
    except KeyboardInterrupt:
        await loop.stop()


async def _logs(service: str, query: str, since: str, backend_name: str, limit: int) -> None:
    from datetime import timezone, datetime
    from tinker.backends import get_backend

    backend = get_backend(backend_name)
    end = datetime.now(timezone.utc)
    start = backend._parse_since(since)

    with console.status(f"Querying {backend_name}..."):
        entries = await backend.query_logs(service, query, start, end, limit)

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Timestamp", style="dim", width=24)
    table.add_column("Level", width=8)
    table.add_column("Message")

    level_styles = {"ERROR": "red", "CRITICAL": "bold red", "WARN": "yellow", "INFO": "green"}
    for entry in entries:
        style = level_styles.get(entry.level, "white")
        table.add_row(
            entry.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            f"[{style}]{entry.level}[/{style}]",
            entry.message[:200],
        )

    console.print(table)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _print_report(report: object) -> None:
    from tinker.agent.orchestrator import IncidentReport
    assert isinstance(report, IncidentReport)

    color = SEVERITY_COLORS.get(report.severity, "white")
    console.print(
        Panel(
            f"[{color}]SEVERITY: {report.severity.upper()}[/{color}]\n\n"
            f"[bold]Root Cause:[/bold]\n{report.root_cause}\n\n"
            f"[bold]Affected Services:[/bold] {', '.join(report.affected_services)}",
            title=f"[bold]Incident Report — {report.incident_id}[/bold]",
            border_style=color,
        )
    )
    if report.suggested_fix:
        console.print(Markdown(f"### Suggested Fix\n\n{report.suggested_fix}"))
        console.print(f"\n[dim]Run: tinker fix {report.incident_id} --approve to apply[/dim]")


if __name__ == "__main__":
    app()

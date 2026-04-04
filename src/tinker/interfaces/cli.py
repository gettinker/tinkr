"""Tinker CLI."""

from __future__ import annotations

import asyncio
import sys
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
    no_args_is_help=True,
    rich_markup_mode="rich",
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


def _get_client():
    from tinker.client import get_client
    try:
        return get_client()
    except Exception as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(1)


# ── Server command ────────────────────────────────────────────────────────────

@app.command()
def server(
    host: str = typer.Option("0.0.0.0", "--host", help="Bind host"),
    port: int = typer.Option(8000, "--port", "-p", help="Bind port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev only)"),
    log_level: str = typer.Option("info", "--log-level", help="Uvicorn log level"),
) -> None:
    """[bold cyan]Start the Tinker server.[/bold cyan]

    Run this on any machine that has access to your cloud observability stack
    (CloudWatch, GCP, Azure, Grafana, Datadog, etc.). The server exposes a REST API
    that the CLI connects to.

    Examples:

      tinker server
      tinker server --port 9000
      tinker server --host 127.0.0.1 --reload   # dev mode
    """
    import uvicorn

    console.print(Panel.fit(
        f"[bold cyan]Tinker Server[/bold cyan]  [dim]v{__version__}[/dim]\n"
        f"Listening on [bold]http://{host}:{port}[/bold]\n"
        f"Docs: [link]http://{host}:{port}/docs[/link]",
        border_style="cyan",
    ))

    uvicorn.run(
        "tinker.server.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        log_level=log_level,
    )


# ── Setup commands ────────────────────────────────────────────────────────────

init_app = typer.Typer(help="Interactive setup wizards.")
app.add_typer(init_app, name="init")


@init_app.command("server")
def init_server() -> None:
    """[bold cyan]Set up a Tinker server on this machine.[/bold cyan]

    Auto-detects cloud environment, checks observability permissions,
    configures Slack (optional), and writes [bold]~/.tinker/.env[/bold].

    Examples:

      tinker init server
    """
    from tinker.interfaces.init_wizard import ServerWizard
    ServerWizard().run()


@init_app.command("cli")
def init_cli() -> None:
    """[bold cyan]Connect this machine's CLI to a Tinker server.[/bold cyan]

    Asks for the server URL and API token, tests the connection,
    and writes ~/.tinker/config.

    Examples:

      tinker init cli
    """
    from tinker.interfaces.init_wizard import CLIWizard
    CLIWizard().run()


@app.command()
def doctor() -> None:
    """[bold cyan]Verify connectivity to the configured Tinker server.[/bold cyan]"""
    asyncio.run(_doctor())


# ── Analysis commands ─────────────────────────────────────────────────────────

@app.command()
def analyze(
    service: str = typer.Argument(..., help="Service or application name"),
    since: str = typer.Option("1h", "--since", "-s", help="Time window: 1h, 30m, 2d"),
    deep: bool = typer.Option(False, "--deep", help="Use deep RCA model with extended thinking"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Stream agent reasoning"),
) -> None:
    """[bold cyan]Analyze a service for incidents and produce a root cause report.[/bold cyan]

    Examples:

      tinker analyze payments-api
      tinker analyze auth-service --since 2h --verbose
      tinker analyze orders-api --deep
    """
    asyncio.run(_analyze(service, since, deep, verbose))


@app.command()
def fix(
    incident_id: str = typer.Argument(..., help="Incident ID from a previous analyze run"),
    approve: bool = typer.Option(
        False, "--approve", help="Apply the fix and open a PR (requires explicit flag)"
    ),
) -> None:
    """[bold cyan]Display or apply the suggested fix for an incident.[/bold cyan]

    Examples:

      tinker fix INC-abc123
      tinker fix INC-abc123 --approve
    """
    asyncio.run(_fix(incident_id, approve))


@app.command()
def logs(
    service: str = typer.Argument(..., help="Service name"),
    query: str = typer.Option("*", "--query", "-q", help="Log query string"),
    since: str = typer.Option("30m", "--since", "-s"),
    limit: int = typer.Option(50, "--limit", "-n"),
    resource: Optional[str] = typer.Option(None, "--resource", "-r", help="Resource type: ecs, lambda, eks, rds, cloudrun, aks, aca"),
) -> None:
    """[bold cyan]Fetch recent logs for a service.[/bold cyan]

    Examples:

      tinker logs payments-api
      tinker logs payments-api --resource ecs -q "level:ERROR" --since 1h
    """
    asyncio.run(_logs(service, query, since, limit, resource))


@app.command()
def tail(
    service: str = typer.Argument(..., help="Service name"),
    query: str = typer.Option("*", "--query", "-q", help="Filter query"),
    poll: float = typer.Option(2.0, "--poll", "-p", help="Poll interval in seconds"),
    resource: Optional[str] = typer.Option(None, "--resource", "-r", help="Resource type"),
) -> None:
    """[bold cyan]Stream live logs for a service (Ctrl-C to stop).[/bold cyan]

    Examples:

      tinker tail payments-api
      tinker tail auth-service -q 'level:(ERROR OR WARN)'
    """
    asyncio.run(_tail(service, query, poll, resource))


@app.command()
def metrics(
    service: str = typer.Argument(..., help="Service name"),
    metric: str = typer.Argument(..., help="Metric name"),
    since: str = typer.Option("1h", "--since", "-s"),
    resource: Optional[str] = typer.Option(None, "--resource", "-r"),
) -> None:
    """[bold cyan]Show metric values for a service.[/bold cyan]

    Examples:

      tinker metrics payments-api Errors
      tinker metrics auth-service http_requests_total --since 2h
    """
    asyncio.run(_metrics(service, metric, since, resource))


@app.command()
def anomaly(
    service: str = typer.Argument(..., help="Service name"),
    since: str = typer.Option("1h", "--since", "-s", help="Look-back window: 30m, 1h, 2h, 1d"),
    severity: Optional[str] = typer.Option(None, "--severity", help="Filter: low/medium/high/critical"),
    resource: Optional[str] = typer.Option(None, "--resource", "-r"),
    json_out: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """[bold cyan]Detect anomalies for a service (fast, no LLM).[/bold cyan]

    Examples:

      tinker anomaly payments-api
      tinker anomaly payments-api --since 2h --severity high
      tinker anomaly payments-api --json
    """
    asyncio.run(_anomaly(service, since, severity, resource, json_out))


@app.command()
def monitor(
    service: str = typer.Argument(..., help="Service name"),
    since: str = typer.Option("1h", "--since", "-s", help="Initial look-back window"),
    resource: Optional[str] = typer.Option(None, "--resource", "-r"),
) -> None:
    """[bold cyan]Open interactive anomaly monitor REPL.[/bold cyan]

    Subcommands inside the REPL:

      explain <n>   — LLM explains the anomaly
      fix <n>       — LLM proposes a code fix
      approve       — Apply fix and open a GitHub PR
      refresh       — Re-fetch anomalies
      help          — Show all commands

    Examples:

      tinker monitor payments-api
      tinker monitor payments-api --since 2h
    """
    asyncio.run(_monitor_repl(service, since))


# ── Watch commands ────────────────────────────────────────────────────────────

watch_app = typer.Typer(help="Manage server-side anomaly watches.")
app.add_typer(watch_app, name="watch")


@watch_app.command("start")
def watch_start(
    service: str = typer.Argument(..., help="Service to watch"),
    channel: Optional[str] = typer.Option(
        None, "--channel", "-c",
        help="Slack channel for alerts, e.g. #incidents (uses server default if omitted)",
    ),
    interval: int = typer.Option(60, "--interval", "-i", help="Poll interval in seconds"),
) -> None:
    """[bold cyan]Start a watch for a service on the Tinker server.[/bold cyan]

    The server polls for anomalies on a schedule and posts to Slack when
    the anomaly set changes.

    Examples:

      tinker watch start payments-api --channel "#incidents"
      tinker watch start auth-service --interval 120
    """
    asyncio.run(_watch_start(service, channel, interval))


@watch_app.command("list")
def watch_list() -> None:
    """[bold cyan]List watches on the Tinker server.[/bold cyan]"""
    asyncio.run(_watch_list())


@watch_app.command("stop")
def watch_stop(
    watch_id: str = typer.Argument(..., help="Watch ID from 'tinker watch list'"),
) -> None:
    """[bold cyan]Stop a watch on the Tinker server.[/bold cyan]"""
    asyncio.run(_watch_stop(watch_id))


@app.command()
def version() -> None:
    """Print the Tinker version."""
    console.print(f"tinker {__version__}")


# ── Async implementations ─────────────────────────────────────────────────────

async def _doctor() -> None:
    client = _get_client()
    console.print(Panel.fit("[bold]Tinker Doctor[/bold]", border_style="cyan"))
    console.print()
    results: list[tuple[str, bool, str]] = []

    try:
        data = await client.health()
        results.append(("Server", True, f"v{data.get('version','')}  backend={data.get('backend','')}"))
    except Exception as exc:
        results.append(("Server", False, str(exc)[:80]))
        _print_doctor_table(results)
        console.print()
        console.print(
            "[red]Cannot reach Tinker server.[/red]\n"
            "[dim]Run [bold]tinker server[/bold] on the target machine, "
            "then [bold]tinker init cli[/bold] to point this CLI at it.[/dim]"
        )
        raise typer.Exit(1)

    try:
        from datetime import timezone, timedelta, datetime as dt
        end = dt.now(timezone.utc)
        start = end - timedelta(minutes=5)
        await client.query_logs("_health_check_", "*", start, end, limit=1)
        results.append(("Backend", True, "query ok"))
    except Exception as exc:
        msg = str(exc)[:60]
        is_ok = not any(w in msg.lower() for w in ["auth", "credential", "403", "401"])
        results.append(("Backend", is_ok, msg))

    _print_doctor_table(results)


def _print_doctor_table(results: list[tuple[str, bool, str]]) -> None:
    table = Table(show_header=True, header_style="bold")
    table.add_column("Check", width=12)
    table.add_column("Status", width=8)
    table.add_column("Detail")
    all_ok = True
    for name, ok, detail in results:
        status = "[green]✓ OK[/green]" if ok else "[red]✗ FAIL[/red]"
        if not ok:
            all_ok = False
        table.add_row(name, status, detail)
    console.print(table)
    console.print()
    if all_ok:
        console.print("[green bold]All checks passed.[/green bold]")
    else:
        console.print("[red]Some checks failed. Review the details above.[/red]")
        raise typer.Exit(1)


async def _analyze(service: str, since: str, deep: bool, verbose: bool) -> None:
    client = _get_client()
    console.print(Panel(
        f"[bold]Analyzing[/bold] [cyan]{service}[/cyan] · last {since}"
        + (" · [yellow]deep mode[/yellow]" if deep else ""),
        expand=False,
    ))
    if verbose:
        console.print("[dim]Streaming agent reasoning...[/dim]\n")
        async for chunk in await client.stream_analyze(service, since, deep):
            if isinstance(chunk, str):
                console.print(chunk, end="")
        console.print()
    else:
        with console.status(f"[bold green]Running RCA on {service}...[/bold green]"):
            report = await client.analyze(service, since, deep)
        _print_report(report)


async def _fix(incident_id: str, approve: bool) -> None:
    console.print(f"[yellow]Looking up fix for[/yellow] {incident_id}...")
    if approve:
        confirmed = typer.confirm(
            f"\nApply fix for {incident_id} and open a PR? This cannot be undone.",
            default=False,
        )
        if not confirmed:
            console.print("[red]Aborted.[/red]")
            raise typer.Exit(1)
        console.print("[green]Applying fix...[/green]")
    else:
        console.print("[dim]Re-run with --approve to apply.[/dim]")


async def _logs(service: str, query: str, since: str, limit: int, resource_type: str | None = None) -> None:
    client = _get_client()
    end = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    start = client.parse_since(since)
    with console.status("Querying..."):
        entries = await client.query_logs(service, query, start, end, limit, resource_type=resource_type)
    if not entries:
        console.print("[dim]No log entries found.[/dim]")
        return
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Timestamp", style="dim", width=20)
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


async def _tail(service: str, query: str, poll: float, resource_type: str | None = None) -> None:
    client = _get_client()
    level_styles = {"ERROR": "red", "CRITICAL": "bold red", "WARN": "yellow", "WARNING": "yellow", "INFO": "green", "DEBUG": "dim"}
    console.print(
        f"[bold green]Tailing[/bold green] [cyan]{service}[/cyan]"
        + (f" · [dim]{query}[/dim]" if query != "*" else "")
        + "  [dim](Ctrl-C to stop)[/dim]"
    )
    console.print()
    try:
        async for entry in await client.tail_logs(service, query, poll_interval=poll, resource_type=resource_type):
            style = level_styles.get(entry.level.upper(), "white")
            ts = entry.timestamp.strftime("%H:%M:%S")
            level = f"[{style}]{entry.level:<8}[/{style}]"
            console.print(f"[dim]{ts}[/dim]  {level}  {entry.message[:200]}")
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")


async def _metrics(service: str, metric: str, since: str, resource_type: str | None = None) -> None:
    client = _get_client()
    end = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    start = client.parse_since(since)
    with console.status("Querying..."):
        points = await client.get_metrics(service, metric, start, end, resource_type=resource_type)
    if not points:
        console.print("[dim]No metric data found.[/dim]")
        return
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Timestamp", style="dim")
    table.add_column("Value", justify="right")
    for p in points[-20:]:
        table.add_row(p.timestamp.strftime("%H:%M:%S"), f"{p.value:.4g}")
    console.print(table)


async def _anomaly(
    service: str,
    since: str,
    severity: str | None,
    resource_type: str | None,
    json_out: bool,
) -> None:
    import json as json_mod

    client = _get_client()
    unit = since[-1]
    val = int(since[:-1])
    match unit:
        case "m": window = val
        case "h": window = val * 60
        case "d": window = val * 1440
        case _:
            console.print("[red]Unknown time unit in --since. Use m/h/d.[/red]")
            raise typer.Exit(1)

    with console.status(f"[bold green]Detecting anomalies for {service}...[/bold green]"):
        anomalies = await client.detect_anomalies(service, window_minutes=window)

    if severity:
        anomalies = [a for a in anomalies if a.severity.lower() == severity.lower()]

    if json_out:
        print(json_mod.dumps([a.to_dict() for a in anomalies], indent=2, default=str))
        return

    if not anomalies:
        console.print(f"[dim]No anomalies detected for {service} in the last {since}.[/dim]")
        return

    table = Table(
        show_header=True,
        header_style="bold magenta",
        title=f"Anomalies — {service} (last {since})",
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
    console.print(
        f"[dim]Run [bold]tinker monitor {service}[/bold] to explain and fix anomalies.[/dim]"
    )


async def _monitor_repl(service: str, since: str) -> None:
    unit = since[-1]
    val = int(since[:-1])
    match unit:
        case "m": window = val
        case "h": window = val * 60
        case "d": window = val * 1440
        case _:
            console.print("[red]Unknown time unit in --since. Use m/h/d.[/red]")
            raise typer.Exit(1)

    from tinker.interfaces.monitor_repl import MonitorREPL
    client = _get_client()
    repl = MonitorREPL(service=service, client=client, window_minutes=window)
    await repl.run()


async def _watch_start(service: str, channel: str | None, interval: int) -> None:
    client = _get_client()
    with console.status(f"[bold green]Creating watch for {service}...[/bold green]"):
        watch = await client.create_watch(
            service=service,
            slack_channel=channel,
            interval_seconds=interval,
        )
    watch_id = watch.get("watch_id", "?")
    console.print(
        f"[green]Watch started[/green]  [bold]{watch_id}[/bold]\n"
        f"  service=[cyan]{service}[/cyan]  "
        f"channel={watch.get('slack_channel') or '—'}  "
        f"interval={interval}s\n"
        f"[dim]Stop with: tinker watch stop {watch_id}[/dim]"
    )


async def _watch_list() -> None:
    client = _get_client()
    watches = await client.list_watches()
    if not watches:
        console.print("[dim]No watches on the server.[/dim]")
        return
    table = Table(show_header=True, header_style="bold magenta", title="Server Watches")
    table.add_column("ID", width=16)
    table.add_column("Service", width=20)
    table.add_column("Status", width=9)
    table.add_column("Channel", width=16)
    table.add_column("Interval", width=10)
    table.add_column("Last Run")
    for w in watches:
        status = w.get("status", "?")
        scolor = "green" if status == "running" else "dim"
        table.add_row(
            w["watch_id"],
            w["service"],
            f"[{scolor}]{status}[/{scolor}]",
            w.get("slack_channel") or "—",
            f"{w['interval_seconds']}s",
            (w.get("last_run_at") or "never")[:19],
        )
    console.print(table)


async def _watch_stop(watch_id: str) -> None:
    client = _get_client()
    try:
        await client.stop_watch(watch_id)
        console.print(f"[green]Watch {watch_id} stopped.[/green]")
    except Exception as exc:
        console.print(f"[red]Failed to stop watch: {exc}[/red]")
        raise typer.Exit(1)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _print_report(report: object) -> None:
    from tinker.agent.orchestrator import IncidentReport
    assert isinstance(report, IncidentReport)
    color = SEVERITY_COLORS.get(report.severity, "white")
    console.print(Panel(
        f"[{color}]SEVERITY: {report.severity.upper()}[/{color}]\n\n"
        f"[bold]Root Cause:[/bold]\n{report.root_cause}\n\n"
        f"[bold]Affected Services:[/bold] {', '.join(report.affected_services)}\n"
        f"[bold]Model:[/bold] [dim]{report.model_used}[/dim]",
        title=f"[bold]Incident Report — {report.incident_id}[/bold]",
        border_style=color,
    ))
    if report.suggested_fix:
        console.print(Markdown(f"### Suggested Fix\n\n{report.suggested_fix}"))
        console.print(f"\n[dim]Run: tinker fix {report.incident_id} --approve to apply[/dim]")


if __name__ == "__main__":
    app()

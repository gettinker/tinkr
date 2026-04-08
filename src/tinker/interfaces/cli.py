"""Tinkr CLI.

Structure
---------
Each command is three lines:
  1. Typer decorator — defines the public interface (flags, help, types)
  2. Handler call    — fetches / filters data via interfaces/handlers.py
  3. Renderer call   — formats result via interfaces/renderers.py

Adding a new command
--------------------
1. Add a handler function in interfaces/handlers.py.
2. Add a render function in interfaces/renderers.py.
3. Add a @app.command() here that wires them together.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import structlog
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from tinker import __version__
from tinker.interfaces.renderers import OutputFormat

app = typer.Typer(
    name="tinker",
    help="AI-powered observability and incident response agent.",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()
log = structlog.get_logger(__name__)


# ── Error handling ────────────────────────────────────────────────────────────

def _run(coro) -> None:
    """Run a coroutine, printing server errors cleanly instead of a traceback."""
    import httpx
    try:
        asyncio.run(coro)
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", str(exc))
        except Exception:
            detail = str(exc)
        console.print(f"[red]Server error:[/red] {detail}")
        raise typer.Exit(1)
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


def _get_client():
    from tinker.client import get_client
    try:
        return get_client()
    except Exception as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(1)


# ── Setup commands ────────────────────────────────────────────────────────────

@app.command("init")
def init_cli() -> None:
    """[bold cyan]Connect this machine's CLI to a Tinkr server.[/bold cyan]

    Asks for the server URL and API token, tests the connection,
    and writes [bold]~/.tinkr/config[/bold].

    To set up the server itself, run [bold]tinkr-server init[/bold] on the server machine.
    """
    from tinker.interfaces.init_wizard import CLIWizard
    CLIWizard().run()


# ── Doctor ────────────────────────────────────────────────────────────────────

@app.command()
def doctor() -> None:
    """[bold cyan]Verify connectivity to the configured Tinkr server.[/bold cyan]"""
    _run(_doctor())


async def _doctor() -> None:
    client = _get_client()
    console.print(Panel.fit("[bold]Tinkr Doctor[/bold]", border_style="cyan"))
    console.print()
    results: list[tuple[str, bool, str]] = []

    try:
        data = await client.health()
        results.append(("Server", True, f"v{data.get('version','')}  backend={data.get('backend','')}"))
    except Exception as exc:
        results.append(("Server", False, str(exc)[:80]))
        _print_check_table(results)
        console.print()
        console.print(
            "[red]Cannot reach Tinkr server.[/red]\n"
            "[dim]Run [bold]tinkr-server[/bold] on the server machine, "
            "then [bold]tinkr init[/bold] to point this CLI at it.[/dim]"
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

    _print_check_table(results)


def _print_check_table(results: list[tuple[str, bool, str]]) -> None:
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


# ── Logs ──────────────────────────────────────────────────────────────────────

@app.command()
def logs(
    service: str = typer.Argument(..., help="Service name"),
    query: str = typer.Option("*", "--query", "-q", help="Log query string"),
    since: str = typer.Option("30m", "--since", "-s"),
    limit: int = typer.Option(50, "--limit", "-n"),
    resource: Optional[str] = typer.Option(None, "--resource", "-r", help="Resource type: ecs, lambda, eks, rds, cloudrun, aks"),
    output: OutputFormat = typer.Option(OutputFormat.table, "--output", "-o", help="Output format"),
) -> None:
    """[bold cyan]Fetch recent logs for a service.[/bold cyan]

    Examples:

      tinker logs payments-api
      tinker logs payments-api --resource ecs -q "level:ERROR" --since 1h
      tinker logs payments-api --output jsonlines | jq .message
    """
    _run(_logs(service, query, since, limit, resource, output))


async def _logs(service, query, since, limit, resource, output) -> None:
    from tinker.interfaces.handlers import get_logs
    from tinker.interfaces.renderers import render_logs
    client = _get_client()
    with console.status("Querying..."):
        entries = await get_logs(client, service, query, since, limit, resource)
    render_logs(entries, output)


# ── Tail ──────────────────────────────────────────────────────────────────────

@app.command()
def tail(
    service: str = typer.Argument(..., help="Service name"),
    query: str = typer.Option("*", "--query", "-q", help="Filter query"),
    poll: float = typer.Option(2.0, "--poll", "-p", help="Poll interval in seconds"),
    resource: Optional[str] = typer.Option(None, "--resource", "-r", help="Resource type"),
    output: OutputFormat = typer.Option(OutputFormat.table, "--output", "-o", help="Output format"),
) -> None:
    """[bold cyan]Stream live logs for a service (Ctrl-C to stop).[/bold cyan]

    Examples:

      tinker tail payments-api
      tinker tail auth-service -q 'level:(ERROR OR WARN)'
      tinker tail payments-api --output jsonlines | jq .message
    """
    _run(_tail(service, query, poll, resource, output))


async def _tail(service, query, poll, resource, output) -> None:
    from tinker.interfaces.handlers import stream_logs
    from tinker.interfaces.renderers import render_log_entry
    client = _get_client()
    if output == OutputFormat.table:
        console.print(
            f"[bold green]Tailing[/bold green] [cyan]{service}[/cyan]"
            + (f" · [dim]{query}[/dim]" if query != "*" else "")
            + "  [dim](Ctrl-C to stop)[/dim]"
        )
        console.print()
    try:
        async for entry in stream_logs(client, service, query, poll, resource):
            render_log_entry(entry, output)
    except KeyboardInterrupt:
        if output == OutputFormat.table:
            console.print("\n[dim]Stopped.[/dim]")


# ── Metrics ───────────────────────────────────────────────────────────────────

@app.command()
def metrics(
    service: str = typer.Argument(..., help="Service name"),
    metric: str = typer.Argument(..., help="Metric name"),
    since: str = typer.Option("1h", "--since", "-s"),
    resource: Optional[str] = typer.Option(None, "--resource", "-r"),
    output: OutputFormat = typer.Option(OutputFormat.table, "--output", "-o", help="Output format"),
) -> None:
    """[bold cyan]Show metric values for a service.[/bold cyan]

    Examples:

      tinker metrics payments-api Errors
      tinker metrics auth-service http_requests_total --since 2h
      tinker metrics payments-api Errors --output json
    """
    _run(_metrics(service, metric, since, resource, output))


async def _metrics(service, metric, since, resource, output) -> None:
    from tinker.interfaces.handlers import get_metrics
    from tinker.interfaces.renderers import render_metrics
    client = _get_client()
    with console.status("Querying..."):
        points = await get_metrics(client, service, metric, since, resource)
    render_metrics(points, output)


# ── Anomaly ───────────────────────────────────────────────────────────────────

@app.command()
def anomaly(
    service: str = typer.Argument(..., help="Service name"),
    since: str = typer.Option("1h", "--since", "-s", help="Look-back window: 30m, 1h, 2h, 1d"),
    severity: Optional[str] = typer.Option(None, "--severity", help="Filter: low/medium/high/critical"),
    resource: Optional[str] = typer.Option(None, "--resource", "-r"),
    output: OutputFormat = typer.Option(OutputFormat.table, "--output", "-o", help="Output format"),
) -> None:
    """[bold cyan]Detect anomalies for a service (fast, no LLM).[/bold cyan]

    Examples:

      tinker anomaly payments-api
      tinker anomaly payments-api --since 2h --severity high
      tinker anomaly payments-api --output json
      tinker anomaly payments-api --output jsonlines | jq .severity
    """
    _run(_anomaly(service, since, severity, resource, output))


async def _anomaly(service, since, severity, resource, output) -> None:
    from tinker.interfaces.handlers import get_anomalies
    from tinker.interfaces.renderers import render_anomalies
    client = _get_client()
    with console.status(f"[bold green]Detecting anomalies for {service}...[/bold green]"):
        anomalies = await get_anomalies(client, service, since, severity, resource)
    render_anomalies(anomalies, output, service=service, since=since)


# ── Investigate REPL ─────────────────────────────────────────────────────────

@app.command()
def investigate(
    service: str = typer.Argument(..., help="Service name"),
    since: str = typer.Option("30m", "--since", "-s", help="Look-back window (e.g. 30m, 2h, 1d)"),
    level: str = typer.Option("ERROR", "--level", "-l", help="Log level to group: ERROR, WARN, or ALL"),
    resource: Optional[str] = typer.Option(None, "--resource", "-r", help="Resource type (ecs, lambda, eks…)"),
) -> None:
    """[bold cyan]Investigate errors interactively — group, explain, fix, PR.[/bold cyan]

    Opens a two-level REPL session. Logs are fetched, deduplicated into error
    groups, and displayed. LLM is only invoked when you explicitly type
    [bold]explain[/bold] or [bold]fix[/bold].

    Level 1 — error groups table (pattern + count + stack trace count)
    Level 2 — drill into a group with [bold]logs <n>[/bold] to see individual entries

    Examples:

      tinker investigate payments-api
      tinker investigate payments-api --since 2h
      tinker investigate payments-api --level WARN
    """
    _run(_investigate_repl(service, since, level, resource))


async def _investigate_repl(service: str, since: str, level: str, resource: str | None) -> None:
    from tinker.interfaces.handlers import parse_since
    from tinker.interfaces.investigate_repl import InvestigateREPL
    _, window = parse_since(since)
    client = _get_client()
    await InvestigateREPL(
        service=service, client=client,
        window_minutes=window, level=level, resource=resource,
    ).run()


# ── Watch commands ────────────────────────────────────────────────────────────

watch_app = typer.Typer(help="Manage server-side anomaly watches.")
app.add_typer(watch_app, name="watch")


@watch_app.command("start")
def watch_start(
    service: str = typer.Argument(..., help="Service to watch"),
    notifier: Optional[str] = typer.Option(
        None, "--notifier", "-n",
        help="Notifier name from [notifiers.*] in config.toml (default: 'default')",
    ),
    destination: Optional[str] = typer.Option(
        None, "--destination", "-d",
        help="Platform-specific target override, e.g. '#payments-oncall' for Slack",
    ),
    interval: int = typer.Option(60, "--interval", "-i", help="Poll interval in seconds"),
) -> None:
    """[bold cyan]Start a watch for a service on the Tinkr server.[/bold cyan]

    The server polls for anomalies on a schedule and dispatches alerts via the
    configured notifier when the anomaly set changes.

    Examples:

      tinker watch start payments-api
      tinker watch start payments-api --notifier discord-ops
      tinker watch start payments-api --notifier default --destination "#payments-oncall"
      tinker watch start auth-service --interval 120
    """
    _run(_watch_start(service, notifier, destination, interval))


@watch_app.command("list")
def watch_list(
    output: OutputFormat = typer.Option(OutputFormat.table, "--output", "-o"),
) -> None:
    """[bold cyan]List watches on the Tinkr server.[/bold cyan]"""
    _run(_watch_list(output))


@watch_app.command("stop")
def watch_stop(
    watch_id: str = typer.Argument(..., help="Watch ID from 'tinker watch list'"),
) -> None:
    """[bold cyan]Stop a watch on the Tinkr server.[/bold cyan]"""
    _run(_watch_stop(watch_id))


@watch_app.command("delete")
def watch_delete(
    watch_id: str = typer.Argument(..., help="Watch ID from 'tinker watch list'"),
) -> None:
    """[bold cyan]Permanently delete a watch from the Tinkr server.[/bold cyan]

    Removes the watch record entirely (unlike [bold]stop[/bold] which keeps it as 'stopped').

    Example:

      tinker watch delete watch-3a976e39
    """
    _run(_watch_delete(watch_id))


async def _watch_start(service, notifier, destination, interval) -> None:
    from tinker.interfaces.handlers import start_watch
    client = _get_client()
    with console.status(f"[bold green]Creating watch for {service}...[/bold green]"):
        watch = await start_watch(client, service, notifier, destination, interval)
    watch_id = watch.get("watch_id", "?")
    console.print(
        f"[green]Watch started[/green]  [bold]{watch_id}[/bold]\n"
        f"  service=[cyan]{service}[/cyan]  "
        f"notifier={watch.get('notifier') or 'default'}  "
        f"interval={interval}s\n"
        f"[dim]Stop with: tinker watch stop {watch_id}[/dim]"
    )


async def _watch_list(output) -> None:
    from tinker.interfaces.handlers import get_watches
    from tinker.interfaces.renderers import render_watches
    client = _get_client()
    watches = await get_watches(client)
    render_watches(watches, output)


async def _watch_stop(watch_id) -> None:
    from tinker.interfaces.handlers import stop_watch
    client = _get_client()
    await stop_watch(client, watch_id)
    console.print(f"[green]Watch {watch_id} stopped.[/green]")


async def _watch_delete(watch_id) -> None:
    from tinker.interfaces.handlers import delete_watch
    client = _get_client()
    await delete_watch(client, watch_id)
    console.print(f"[green]Watch {watch_id} deleted.[/green]")


# ── Trace command ────────────────────────────────────────────────────────────

@app.command()
def trace(
    service: str = typer.Argument(..., help="Service name"),
    since: str = typer.Option("1h", "--since", "-s", help="Look-back window: 30m, 1h, 2h"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max traces to return"),
    output: OutputFormat = typer.Option(OutputFormat.table, "--output", "-o"),
) -> None:
    """[bold cyan]Fetch recent distributed traces for a service.[/bold cyan]

    Examples:

      tinker trace payments-api
      tinker trace payments-api --since 30m --limit 50
      tinker trace payments-api --output json
    """
    _run(_trace(service, since, limit, output))


async def _trace(service, since, limit, output) -> None:
    from tinker.interfaces.handlers import get_traces
    from tinker.interfaces.renderers import render_traces
    client = _get_client()
    with console.status(f"[bold green]Fetching traces for {service}...[/bold green]"):
        traces = await get_traces(client, service, since=since, limit=limit)
    render_traces(traces, output, service=service)


# ── Diff command ──────────────────────────────────────────────────────────────

@app.command()
def diff(
    service: str = typer.Argument(..., help="Service name"),
    baseline: str = typer.Option("2h", "--baseline", "-b", help="Baseline window (older period)"),
    compare: str = typer.Option("1h", "--compare", "-c", help="Comparison window (current period)"),
    output: OutputFormat = typer.Option(OutputFormat.table, "--output", "-o"),
) -> None:
    """[bold cyan]Compare error rates and anomalies between two time windows.[/bold cyan]

    Baseline is shifted to end where compare begins, so windows never overlap.

    Examples:

      tinker diff payments-api
      tinker diff payments-api --baseline 24h --compare 1h
      tinker diff auth-service --output json
    """
    _run(_diff(service, baseline, compare, output))


async def _diff(service, baseline, compare, output) -> None:
    from tinker.interfaces.handlers import get_diff
    from tinker.interfaces.renderers import render_diff
    client = _get_client()
    with console.status("[bold green]Comparing windows...[/bold green]"):
        result = await get_diff(client, service, baseline=baseline, compare=compare)
    render_diff(result, output)


# ── RCA command ───────────────────────────────────────────────────────────────

@app.command()
def rca(
    service: str = typer.Argument(..., help="Service name"),
    since: str = typer.Option("1h", "--since", "-s", help="Evidence window: 30m, 1h, 2h"),
    severity: Optional[str] = typer.Option(None, "--severity", help="Min severity to include: low/medium/high/critical"),
) -> None:
    """[bold cyan]Run a full AI root-cause analysis combining logs, metrics, and traces.[/bold cyan]

    Streams a structured RCA report with executive summary, root cause,
    contributing factors, timeline, immediate actions, and prevention.

    Examples:

      tinker rca payments-api
      tinker rca payments-api --since 2h --severity high
    """
    _run(_rca(service, since, severity))


async def _rca(service, since, severity) -> None:
    from rich.markdown import Markdown
    from rich.panel import Panel
    client = _get_client()
    console.print(Panel.fit(
        f"[bold cyan]Root Cause Analysis[/bold cyan]  [dim]{service}  window:{since}[/dim]",
        border_style="cyan",
    ))
    chunks: list[str] = []
    with console.status("[bold green]Analysing logs, metrics, and traces...[/bold green]"):
        # Drain first chunk to dismiss the spinner before streaming
        gen = client.stream_rca(service, since=since, severity_filter=severity)
        first = await gen.__anext__()
        chunks.append(first)
    console.print()
    async for chunk in gen:
        chunks.append(chunk)
        console.print(chunk, end="", highlight=False)
    console.print()


# ── Deploy commands ───────────────────────────────────────────────────────────

deploy_app = typer.Typer(help="Deploy tracking — list commits and correlate with anomalies.")
app.add_typer(deploy_app, name="deploy")


@deploy_app.command("list")
def deploy_list(
    service: str = typer.Argument(..., help="Service name"),
    since: str = typer.Option("7d", "--since", "-s", help="Look-back window"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max commits to show"),
    output: OutputFormat = typer.Option(OutputFormat.table, "--output", "-o"),
) -> None:
    """[bold cyan]List recent deploys (commits) for a service.[/bold cyan]

    Examples:

      tinker deploy list payments-api
      tinker deploy list payments-api --since 14d --limit 20
    """
    _run(_deploy_list(service, since, limit, output))


@deploy_app.command("correlate")
def deploy_correlate(
    service: str = typer.Argument(..., help="Service name"),
    since: str = typer.Option("7d", "--since", "-s", help="Look-back window"),
    output: OutputFormat = typer.Option(OutputFormat.table, "--output", "-o"),
) -> None:
    """[bold cyan]Correlate recent deploys with anomaly spikes.[/bold cyan]

    Deploys with anomalies detected within 30 minutes are highlighted in red.

    Examples:

      tinker deploy correlate payments-api
      tinker deploy correlate payments-api --since 14d
    """
    _run(_deploy_correlate(service, since, output))


async def _deploy_list(service, since, limit, output) -> None:
    from tinker.interfaces.handlers import get_deploys
    from tinker.interfaces.renderers import render_deploys
    client = _get_client()
    with console.status(f"[bold green]Fetching deploys for {service}...[/bold green]"):
        data = await get_deploys(client, service, since=since, limit=limit)
    render_deploys(data, output, correlate=False)


async def _deploy_correlate(service, since, output) -> None:
    from tinker.interfaces.handlers import correlate_deploys
    from tinker.interfaces.renderers import render_deploys
    client = _get_client()
    with console.status("[bold green]Correlating deploys with anomalies...[/bold green]"):
        data = await correlate_deploys(client, service, since=since)
    render_deploys(data, output, correlate=True)


# ── SLO command ───────────────────────────────────────────────────────────────

@app.command()
def slo(
    service: str = typer.Argument(..., help="Service name"),
    target: float = typer.Option(99.9, "--target", "-t", help="SLO target percentage (e.g. 99.9)"),
    window: str = typer.Option("30d", "--window", "-w", help="Measurement window: 7d, 30d, 90d"),
    output: OutputFormat = typer.Option(OutputFormat.table, "--output", "-o"),
) -> None:
    """[bold cyan]Show SLO availability, error budget, and burn rate.[/bold cyan]

    Computed from log-based error rates over the measurement window.
    Burn rate > 1 means budget is being consumed faster than sustainable.

    Examples:

      tinker slo payments-api
      tinker slo payments-api --target 99.5 --window 7d
      tinker slo payments-api --output json
    """
    _run(_slo(service, target, window, output))


async def _slo(service, target, window, output) -> None:
    from tinker.interfaces.handlers import get_slo
    from tinker.interfaces.renderers import render_slo
    client = _get_client()
    with console.status(f"[bold green]Computing SLO for {service}...[/bold green]"):
        result = await get_slo(client, service, target_pct=target, window=window)
    render_slo(result, output)


# ── Alert commands ────────────────────────────────────────────────────────────

alert_app = typer.Typer(help="Manage threshold-based alert rules.")
app.add_typer(alert_app, name="alert")


@alert_app.command("create")
def alert_create(
    service: str = typer.Argument(..., help="Service name"),
    metric: str = typer.Option(..., "--metric", "-m", help="Metric name to watch"),
    operator: str = typer.Option(..., "--op", help="Comparison operator: gt, lt, gte, lte"),
    threshold: float = typer.Option(..., "--threshold", "-t", help="Numeric threshold"),
    severity: str = typer.Option("medium", "--severity", "-s", help="low/medium/high/critical"),
    notifier: Optional[str] = typer.Option(None, "--notifier", "-n", help="Notifier name from config.toml"),
    destination: Optional[str] = typer.Option(None, "--destination", "-d", help="Channel / webhook override"),
) -> None:
    """[bold cyan]Create a threshold-based alert rule.[/bold cyan]

    Examples:

      tinker alert create payments-api --metric error_rate --op gt --threshold 5.0 --severity high
      tinker alert create auth-service --metric latency_p99 --op gt --threshold 500 --notifier slack --destination "#oncall"
    """
    _run(_alert_create(service, metric, operator, threshold, severity, notifier, destination))


@alert_app.command("list")
def alert_list(
    output: OutputFormat = typer.Option(OutputFormat.table, "--output", "-o"),
) -> None:
    """[bold cyan]List all alert rules.[/bold cyan]"""
    _run(_alert_list(output))


@alert_app.command("delete")
def alert_delete(
    alert_id: str = typer.Argument(..., help="Alert ID from 'tinker alert list'"),
) -> None:
    """[bold cyan]Delete an alert rule.[/bold cyan]

    Example:

      tinker alert delete alert-3a976e39
    """
    _run(_alert_delete(alert_id))


@alert_app.command("mute")
def alert_mute(
    alert_id: str = typer.Argument(..., help="Alert ID from 'tinker alert list'"),
    duration: str = typer.Option("1h", "--duration", "-d", help="Mute duration: 30m, 2h, 1d"),
) -> None:
    """[bold cyan]Silence an alert rule for a duration.[/bold cyan]

    Examples:

      tinker alert mute alert-3a976e39
      tinker alert mute alert-3a976e39 --duration 4h
    """
    _run(_alert_mute(alert_id, duration))


async def _alert_create(service, metric, operator, threshold, severity, notifier, destination) -> None:
    from tinker.interfaces.handlers import create_alert
    client = _get_client()
    rule = await create_alert(client, service, metric, operator, threshold, severity, notifier, destination)
    console.print(
        f"[green]Alert created[/green]  [bold]{rule.get('alert_id')}[/bold]\n"
        f"  {service}  {metric} {operator} {threshold}  severity={severity}\n"
        f"[dim]Mute with: tinker alert mute {rule.get('alert_id')}[/dim]"
    )


async def _alert_list(output) -> None:
    from tinker.interfaces.handlers import get_alerts
    from tinker.interfaces.renderers import render_alerts
    client = _get_client()
    alerts = await get_alerts(client)
    render_alerts(alerts, output)


async def _alert_delete(alert_id) -> None:
    from tinker.interfaces.handlers import delete_alert
    client = _get_client()
    await delete_alert(client, alert_id)
    console.print(f"[green]Alert {alert_id} deleted.[/green]")


async def _alert_mute(alert_id, duration) -> None:
    from tinker.interfaces.handlers import mute_alert
    client = _get_client()
    result = await mute_alert(client, alert_id, duration=duration)
    console.print(f"[green]Alert {alert_id} muted until {result.get('muted_until', '?')[:19]}.[/green]")


# ── Profile commands ──────────────────────────────────────────────────────────

profile_app = typer.Typer(help="Manage configuration profiles (cloud backends).")
app.add_typer(profile_app, name="profile")


@profile_app.command("list")
def profile_list() -> None:
    """[bold cyan]List all configured profiles.[/bold cyan]"""
    from tinker import toml_config as tc
    cfg = tc.get()
    if not cfg.profiles:
        console.print(
            "[dim]No profiles configured. Run [bold]tinkr-server init[/bold] first, "
            "or add a profile with [bold]tinker profile add[/bold].[/dim]"
        )
        return
    table = Table(show_header=True, header_style="bold magenta", title="Profiles")
    table.add_column("", width=2)
    table.add_column("Name")
    table.add_column("Backend", width=14)
    table.add_column("Services", width=9, justify="right")
    table.add_column("Notifiers", width=10, justify="right")
    for name, p in cfg.profiles.items():
        active_marker = "[bold green]●[/bold green]" if name == cfg.active_profile else "[dim]○[/dim]"
        table.add_row(
            active_marker, name, p.backend,
            str(len(p.services)), str(len(p.notifiers)),
        )
    console.print(table)
    console.print(
        f"[dim]Active: [bold]{cfg.active_profile or next(iter(cfg.profiles))}[/bold]  "
        "— change with [bold]tinker profile use <name>[/bold][/dim]"
    )


@profile_app.command("use")
def profile_use(
    name: str = typer.Argument(..., help="Profile name to activate"),
) -> None:
    """[bold cyan]Set the active profile.[/bold cyan]"""
    from tinker import toml_config as tc
    cfg = tc.get()
    if name not in cfg.profiles:
        names = ", ".join(cfg.profiles) or "none"
        console.print(
            f"[red]Profile '{name}' not found.[/red] "
            f"Available: {names}"
        )
        raise typer.Exit(1)
    _set_active_profile(name)
    tc.reload()
    console.print(f"[green]✓ Active profile:[/green] [bold]{name}[/bold]")


@profile_app.command("add")
def profile_add() -> None:
    """[bold cyan]Add a new cloud profile interactively.[/bold cyan]"""
    from tinker.interfaces.init_wizard import ServerWizard
    ServerWizard().run_add_profile()


def _set_active_profile(name: str) -> None:
    """Overwrite the active_profile key in config.toml."""
    import re
    from pathlib import Path
    toml_file = Path.home() / ".tinkr" / "config.toml"
    if not toml_file.exists():
        console.print("[red]config.toml not found. Run [bold]tinkr-server init[/bold] first.[/red]")
        raise typer.Exit(1)
    text = toml_file.read_text(encoding="utf-8")
    new_line = f'active_profile = "{name}"'
    if re.search(r"^active_profile\s*=", text, re.MULTILINE):
        text = re.sub(r"^active_profile\s*=.*$", new_line, text, flags=re.MULTILINE)
    else:
        # Insert after header comment block
        lines = text.splitlines(keepends=True)
        insert_at = 0
        for i, line in enumerate(lines):
            if line.startswith("#") or line.strip() == "":
                insert_at = i + 1
            else:
                break
        lines.insert(insert_at, f"{new_line}\n\n")
        text = "".join(lines)
    toml_file.write_text(text, encoding="utf-8")


# ── Version ───────────────────────────────────────────────────────────────────

@app.command()
def version() -> None:
    """Print the Tinkr version."""
    console.print(f"tinker {__version__}")


if __name__ == "__main__":
    app()

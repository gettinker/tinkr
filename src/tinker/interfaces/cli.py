"""Tinker CLI.

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


# ── Server command ────────────────────────────────────────────────────────────

@app.command()
def server(
    host: str = typer.Option("0.0.0.0", "--host", help="Bind host"),
    port: int = typer.Option(8000, "--port", "-p", help="Bind port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev only)"),
    log_level: str = typer.Option("info", "--log-level", help="Uvicorn log level"),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging — shows every query fired to observability backends"),
) -> None:
    """[bold cyan]Start the Tinker server.[/bold cyan]

    Run this on any machine that has access to your cloud observability stack.
    The server exposes a REST API that the CLI connects to.

    Examples:

      tinker server
      tinker server --port 9000
      tinker server --host 127.0.0.1 --reload   # dev mode
      tinker server --debug                      # show backend queries
    """
    import logging
    import uvicorn

    if debug:
        log_level = "debug"
        logging.basicConfig(level=logging.DEBUG)
        for _noisy in (
            "httpcore", "httpx", "hpack", "h11", "h2",
            "botocore", "boto3", "urllib3", "asyncio",
            "google.auth", "google.api_core",
            "azure.core", "azure.identity",
        ):
            logging.getLogger(_noisy).setLevel(logging.WARNING)

    console.print(Panel.fit(
        f"[bold cyan]Tinker Server[/bold cyan]  [dim]v{__version__}[/dim]\n"
        f"Listening on [bold]http://{host}:{port}[/bold]\n"
        f"Docs: [link]http://{host}:{port}/docs[/link]"
        + ("\n[yellow]debug mode — backend queries will be logged[/yellow]" if debug else ""),
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
    configures Slack / notifiers, generates API key, and writes
    [bold]~/.tinker/config.toml[/bold] + [bold]~/.tinker/.env[/bold].
    """
    from tinker.interfaces.init_wizard import ServerWizard
    ServerWizard().run()


@init_app.command("cli")
def init_cli() -> None:
    """[bold cyan]Connect this machine's CLI to a Tinker server.[/bold cyan]

    Asks for the server URL and API token, tests the connection,
    and writes [bold]~/.tinker/config[/bold].
    """
    from tinker.interfaces.init_wizard import CLIWizard
    CLIWizard().run()


# ── Doctor ────────────────────────────────────────────────────────────────────

@app.command()
def doctor() -> None:
    """[bold cyan]Verify connectivity to the configured Tinker server.[/bold cyan]"""
    _run(_doctor())


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
        _print_check_table(results)
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


# ── Monitor REPL ──────────────────────────────────────────────────────────────

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
    _run(_monitor_repl(service, since, resource))


async def _monitor_repl(service: str, since: str, resource: str | None) -> None:
    from tinker.interfaces.handlers import parse_since
    from tinker.interfaces.monitor_repl import MonitorREPL
    _, window = parse_since(since)
    client = _get_client()
    await MonitorREPL(service=service, client=client, window_minutes=window, resource=resource).run()


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
    """[bold cyan]Start a watch for a service on the Tinker server.[/bold cyan]

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
    """[bold cyan]List watches on the Tinker server.[/bold cyan]"""
    _run(_watch_list(output))


@watch_app.command("stop")
def watch_stop(
    watch_id: str = typer.Argument(..., help="Watch ID from 'tinker watch list'"),
) -> None:
    """[bold cyan]Stop a watch on the Tinker server.[/bold cyan]"""
    _run(_watch_stop(watch_id))


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


# ── Version ───────────────────────────────────────────────────────────────────

@app.command()
def version() -> None:
    """Print the Tinker version."""
    console.print(f"tinker {__version__}")


if __name__ == "__main__":
    app()

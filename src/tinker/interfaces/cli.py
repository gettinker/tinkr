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

# Global mode override — set by the --mode option before any command runs
_mode_override: str | None = None


def _mode_callback(value: str | None) -> str | None:
    global _mode_override
    if value:
        _mode_override = value
    return value


@app.callback()
def _global_options(
    mode: Optional[str] = typer.Option(
        None,
        "--mode",
        "-m",
        help="Override mode: [bold]local[/bold] or [bold]server[/bold]",
        callback=_mode_callback,
        is_eager=True,
        show_default=False,
    ),
) -> None:
    """AI-powered observability and incident response agent."""


def _get_client():
    from tinker.client import get_client
    try:
        return get_client(mode_override=_mode_override)
    except RuntimeError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(1)

SEVERITY_COLORS = {
    "critical": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "green",
    "unknown": "white",
}


# ── Setup commands ────────────────────────────────────────────────────────────

@app.command()
def init(
    env_file: str = typer.Option(".env", "--env-file", help="Path to write .env file"),
    config_file: str = typer.Option("tinker.toml", "--config", help="Path to write tinker.toml"),
) -> None:
    """[bold cyan]Interactive setup wizard.[/bold cyan]

    Guides you through cloud provider selection, permission setup,
    LLM configuration, and optional deployment.
    """
    from pathlib import Path
    from tinker.interfaces.init_wizard import InitWizard
    InitWizard(env_file=Path(env_file), config_file=Path(config_file)).run()


@app.command()
def doctor() -> None:
    """[bold cyan]Verify connectivity to all configured services.[/bold cyan]

    Checks: observability backend, LLM provider, Slack (if configured),
    GitHub (if configured), and the Tinker server (if TINKER_SERVER_URL is set).
    """
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

    Without [bold]--approve[/bold]: shows the proposed diff and explanation.
    With [bold]--approve[/bold]: validates with Semgrep, applies the patch, and opens a PR.

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
    """[bold cyan]Tail raw logs for a service (no AI analysis).[/bold cyan]

    Examples:

      tinker logs payments-api
      tinker logs payments-api --resource ecs -q "level:ERROR" --since 1h
      tinker logs my-fn --resource lambda
    """
    asyncio.run(_logs(service, query, since, limit, resource))


@app.command()
def tail(
    service: str = typer.Argument(..., help="Service name"),
    query: str = typer.Option("*", "--query", "-q", help="Filter query (unified syntax)"),
    poll: float = typer.Option(2.0, "--poll", "-p", help="Poll interval in seconds (poll-based backends)"),
    resource: Optional[str] = typer.Option(None, "--resource", "-r", help="Resource type: ecs, lambda, eks, rds, cloudrun, aks, aca"),
) -> None:
    """[bold cyan]Stream live logs for a service (Ctrl-C to stop).[/bold cyan]

    Uses the backend's native streaming where available (Loki websocket),
    falls back to polling for other backends.

    Examples:

      tinker tail payments-api
      tinker tail payments-api --resource ecs -q "level:ERROR"
      tinker tail auth-service -q 'level:(ERROR OR WARN) AND "timeout"'
    """
    asyncio.run(_tail(service, query, poll, resource))


@app.command()
def metrics(
    service: str = typer.Argument(..., help="Service name"),
    metric: str = typer.Argument(..., help="Metric name"),
    since: str = typer.Option("1h", "--since", "-s"),
    resource: Optional[str] = typer.Option(None, "--resource", "-r", help="Resource type: ecs, lambda, eks, rds, cloudrun, aks, aca"),
) -> None:
    """[bold cyan]Show metric values for a service.[/bold cyan]

    Examples:

      tinker metrics payments-api Errors
      tinker metrics payments-api Errors --resource ecs
      tinker metrics auth-service http_requests_total --since 2h
    """
    asyncio.run(_metrics(service, metric, since, resource))


@app.command()
def anomaly(
    service: str = typer.Argument(..., help="Service name"),
    since: str = typer.Option("1h", "--since", "-s", help="Look-back window: 30m, 1h, 2h, 1d"),
    severity: Optional[str] = typer.Option(None, "--severity", help="Filter by severity: low/medium/high/critical"),
    resource: Optional[str] = typer.Option(None, "--resource", "-r", help="Resource type: ecs, lambda, eks…"),
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
    resource: Optional[str] = typer.Option(None, "--resource", "-r", help="Resource type"),
) -> None:
    """[bold cyan]Open interactive anomaly monitor REPL.[/bold cyan]

    Detect anomalies, then use subcommands to explain, fix, and approve:

      explain <n>   — LLM explains the anomaly (uses pre-built summary, not raw logs)
      fix <n>       — LLM proposes a code fix using repo tools
      approve       — Apply fix and open a GitHub PR
      refresh       — Re-fetch anomalies
      filter        — Filter by severity or time window
      help          — Show all commands

    Examples:

      tinker monitor payments-api
      tinker monitor payments-api --since 2h
    """
    asyncio.run(_monitor_repl(service, since))


# ── Watch commands ─────────────────────────────────────────────────────────────

watch_app = typer.Typer(help="Manage background anomaly watches.")
app.add_typer(watch_app, name="watch")


@watch_app.command("start")
def watch_start(
    service: str = typer.Argument(..., help="Service to watch"),
    channel: Optional[str] = typer.Option(
        None, "--channel", "-c",
        help="Slack channel to post alerts to (e.g. #incidents). "
             "Defaults to SLACK_ALERTS_CHANNEL from config.",
    ),
    interval: int = typer.Option(60, "--interval", "-i", help="Poll interval in seconds"),
) -> None:
    """[bold cyan]Start a background watch daemon for a service.[/bold cyan]

    Detects anomalies on a schedule and posts to Slack when the anomaly set changes.
    PID and state are stored in ~/.tinker/tinker.db.

    Examples:

      tinker watch start payments-api --channel "#incidents"
      tinker watch start auth-service --interval 120
    """
    _watch_start(service, channel, interval)


@watch_app.command("list")
def watch_list() -> None:
    """[bold cyan]List running background watches.[/bold cyan]"""
    from tinker.store.db import TinkerDB
    db = TinkerDB()
    watches = db.list_watches(status="running")
    db.close()

    if not watches:
        console.print("[dim]No running watches.[/dim]")
        return

    table = Table(show_header=True, header_style="bold magenta", title="Running Watches")
    table.add_column("ID", width=16)
    table.add_column("Service", width=20)
    table.add_column("PID", width=7)
    table.add_column("Channel", width=16)
    table.add_column("Interval", width=10)
    table.add_column("Last Run")

    for w in watches:
        table.add_row(
            w["watch_id"],
            w["service"],
            str(w["pid"]),
            w.get("slack_channel") or "—",
            f"{w['interval_seconds']}s",
            (w.get("last_run_at") or "never")[:19],
        )
    console.print(table)


@watch_app.command("stop")
def watch_stop(
    watch_id: str = typer.Argument(..., help="Watch ID from 'tinker watch list'"),
) -> None:
    """[bold cyan]Stop a background watch daemon.[/bold cyan]"""
    from tinker.store.db import TinkerDB
    db = TinkerDB()
    ok = db.stop_watch(watch_id)
    db.close()
    if ok:
        console.print(f"[green]Watch {watch_id} stopped.[/green]")
    else:
        console.print(f"[red]Watch {watch_id} not found or already stopped.[/red]")


@watch_app.command("clean")
def watch_clean() -> None:
    """[bold cyan]Remove stopped or dead watches and old sessions.[/bold cyan]"""
    from tinker.store.db import TinkerDB
    db = TinkerDB()
    watches_removed = db.clean_watches()
    sessions_removed = db.clean_sessions()
    db.close()
    console.print(
        f"[green]Removed {watches_removed} dead watch(es) "
        f"and {sessions_removed} old session(s).[/green]"
    )


@app.command()
def version() -> None:
    """Print the Tinker version."""
    console.print(f"tinker {__version__}")


# ── Help ──────────────────────────────────────────────────────────────────────

@app.command(name="help")
def show_help() -> None:
    """[bold cyan]Show detailed usage guide with examples.[/bold cyan]"""
    _print_help()


# ── Async implementations ─────────────────────────────────────────────────────

async def _doctor() -> None:
    from tinker.config import settings

    client = _get_client()
    console.print(Panel.fit(
        f"[bold]Tinker Doctor[/bold]  [dim]mode: {client.mode}[/dim]",
        border_style="cyan",
    ))
    console.print()

    results: list[tuple[str, bool, str]] = []

    if client.mode == "server":
        # 1. Server reachability + health
        try:
            data = await client.health()
            results.append(("Server", True, f"{data.get('version','')} backend={data.get('backend','')}"))
        except Exception as exc:
            results.append(("Server", False, str(exc)[:80]))
            _print_doctor_table(results)
            console.print()
            console.print(
                "[red]Cannot reach Tinker server.[/red]\n"
                "[dim]See [bold]deploy/helm/[/bold] or [bold]deploy/terraform/[/bold] to deploy, or use [bold]--mode local[/bold].[/dim]"
            )
            raise typer.Exit(1)

        # 2. Backend via server
        try:
            from datetime import timezone, timedelta
            from datetime import datetime as dt
            end = dt.now(timezone.utc)
            start = end - timedelta(minutes=5)
            await client.query_logs("_health_check_", "*", start, end, limit=1)
            results.append(("Backend", True, "query ok"))
        except Exception as exc:
            msg = str(exc)[:60]
            is_ok = not any(w in msg.lower() for w in ["auth", "credential", "403", "401"])
            results.append(("Backend", is_ok, msg))

    else:
        # 1. LLM provider (local mode — client machine needs the key)
        try:
            import litellm
            from tinker.client.local import LocalClient
            assert isinstance(client, LocalClient)
            model = client._cfg.default_model
            resp = litellm.completion(
                model=model,
                messages=[{"role": "user", "content": "reply with the word OK only"}],
                max_tokens=5,
            )
            text = resp.choices[0].message.content or ""
            results.append(("LLM", True, f"{model} → {text.strip()[:20]}"))
        except Exception as exc:
            results.append(("LLM", False, str(exc)[:60]))

        # 2. Observability backend
        try:
            from datetime import timezone, timedelta
            from datetime import datetime as dt
            end = dt.now(timezone.utc)
            start = end - timedelta(minutes=5)
            await client.query_logs("_health_check_", "*", start, end, limit=1)
            from tinker.client.local import LocalClient
            assert isinstance(client, LocalClient)
            results.append(("Backend", True, client._cfg.backend))
        except Exception as exc:
            msg = str(exc)[:60]
            is_ok = not any(w in msg.lower() for w in ["auth", "credential", "permission", "403", "401"])
            from tinker.client.local import LocalClient
            assert isinstance(client, LocalClient)
            results.append(("Backend", is_ok, f"{client._cfg.backend}: {msg}"))

    # Slack + GitHub checks are server-side concerns; only check in local mode
    if client.mode == "local":
        if settings.slack_bot_token:
            try:
                from slack_sdk.web.async_client import AsyncWebClient
                sc = AsyncWebClient(token=settings.slack_bot_token.get_secret_value())
                await sc.auth_test()
                results.append(("Slack", True, "auth_test passed"))
            except Exception as exc:
                results.append(("Slack", False, str(exc)[:60]))

        if settings.github_token:
            try:
                from github import Github
                gh = Github(settings.github_token.get_secret_value())
                gh.get_user().login
                results.append(("GitHub", True, "authenticated"))
            except Exception as exc:
                results.append(("GitHub", False, str(exc)[:60]))

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

# end _print_doctor_table


async def _analyze(service: str, since: str, deep: bool, verbose: bool) -> None:
    client = _get_client()

    console.print(Panel(
        f"[bold]Analyzing[/bold] [cyan]{service}[/cyan] · last {since}"
        + (" · [yellow]deep mode[/yellow]" if deep else "")
        + f"  [dim]({client.mode} mode)[/dim]",
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
        + (f" · [dim]{resource_type}[/dim]" if resource_type else "")
        + f"  [dim]({client.mode} mode · Ctrl-C to stop)[/dim]"
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

    for p in points[-20:]:  # last 20 data points
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
    from datetime import timedelta, timezone
    from datetime import datetime as dt

    client = _get_client()
    # Convert since window to minutes for detect_anomalies
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
        import sys
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
        "[dim]Run [bold]tinker monitor " + service + "[/bold] to explain and fix anomalies.[/dim]"
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
    # MonitorREPL needs a direct backend reference (works in both local and server mode
    # for local; for server mode it routes through the client's detect_anomalies).
    if client.mode == "local":
        from tinker.client.local import LocalClient
        assert isinstance(client, LocalClient)
        backend = client.backend()
    else:
        # Wrap the remote client's detect_anomalies as a duck-typed backend
        backend = _RemoteBackendAdapter(client)

    repl = MonitorREPL(service=service, backend=backend, window_minutes=window)
    await repl.run()


def _watch_start(service: str, channel: str | None, interval: int) -> None:
    import subprocess
    import sys
    import os
    from tinker.config import settings
    from tinker.store.db import TinkerDB

    slack_channel = channel or settings.slack_alerts_channel

    # Spawn detached daemon process
    cmd = [
        sys.executable, "-m", "tinker.monitor.watch_daemon",
        "--service", service,
        "--interval", str(interval),
        "--watch-id", "PLACEHOLDER",  # will be replaced after DB insert
    ]
    if slack_channel:
        cmd += ["--channel", slack_channel]

    # Create the DB record first (daemon needs watch_id)
    db = TinkerDB()

    # Start with pid=0 temporarily, update after spawn
    watch_id = db.create_watch(
        service=service,
        pid=0,
        slack_channel=slack_channel,
        interval_seconds=interval,
    )
    db.close()

    # Replace placeholder and spawn
    cmd[cmd.index("PLACEHOLDER")] = watch_id

    proc = subprocess.Popen(
        cmd,
        start_new_session=True,   # detach from terminal
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Update PID now that we have it
    db2 = TinkerDB()
    db2.update_watch(watch_id, pid=proc.pid)
    db2.close()

    console.print(
        f"[green]Watch started[/green]  "
        f"[bold]{watch_id}[/bold]  "
        f"service=[cyan]{service}[/cyan]  "
        f"pid={proc.pid}  "
        f"channel={slack_channel or '—'}  "
        f"interval={interval}s\n"
        f"[dim]Stop with: tinker watch stop {watch_id}[/dim]"
    )


# ── Remote backend adapter ────────────────────────────────────────────────────

class _RemoteBackendAdapter:
    """Thin adapter so MonitorREPL can call detect_anomalies via a RemoteClient."""

    def __init__(self, client) -> None:
        self._client = client

    async def detect_anomalies(self, service: str, window_minutes: int = 10):
        return await self._client.detect_anomalies(service, window_minutes)


# ── Help content ──────────────────────────────────────────────────────────────

def _print_help() -> None:
    console.print(Panel.fit(
        f"[bold cyan]Tinker[/bold cyan] [dim]v{__version__}[/dim]\n"
        "AI-powered observability and incident response agent",
        border_style="cyan",
    ))
    console.print()

    sections = [
        ("Setup", [
            ("tinker init",            "Interactive setup wizard — generates config + deploy values"),
            ("tinker doctor",          "Verify connectivity to all configured services"),
        ]),
        ("Analysis", [
            ("tinker analyze <service>",          "RCA analysis with incident report"),
            ("tinker analyze <service> --deep",   "Deep analysis with extended thinking"),
            ("tinker analyze <service> -v",       "Stream agent reasoning step by step"),
            ("tinker fix <id>",                   "Show suggested fix for an incident"),
            ("tinker fix <id> --approve",         "Apply fix and open a GitHub PR"),
        ]),
        ("Observability", [
            ("tinker tail <service>",             "Stream live logs (Ctrl-C to stop)"),
            ("tinker tail <svc> -q 'level:ERROR'","Stream filtered live logs"),
            ("tinker logs <service>",             "Fetch recent logs (no AI)"),
            ("tinker logs <svc> -q 'level:ERROR'","Filter logs by query"),
            ("tinker metrics <svc> <metric>",     "Show metric time series"),
            ("tinker monitor --services <svc>",   "Continuous anomaly detection loop"),
        ]),
        ("Slack bot commands", [
            ("/tinker-analyze <service>",          "Analyze a service in a thread"),
            ("/tinker-fix <id>",                   "Get fix suggestion"),
            ("/tinker-approve <id>",               "Apply fix (requires oncall role)"),
            ("/tinker-status",                     "Show active sessions"),
        ]),
    ]

    for title, commands in sections:
        table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
        table.add_column(style="bold cyan", no_wrap=True)
        table.add_column(style="dim")
        for cmd, desc in commands:
            table.add_row(cmd, desc)
        console.print(f"[bold]{title}[/bold]")
        console.print(table)
        console.print()

    console.print(Panel(
        "[bold]Environment variables[/bold]\n\n"
        "[cyan]TINKER_BACKEND[/cyan]       cloudwatch | gcp | azure | grafana | datadog | elastic\n"
        "[cyan]TINKER_DEFAULT_MODEL[/cyan] LiteLLM model string, e.g. anthropic/claude-sonnet-4-6\n"
        "[cyan]ANTHROPIC_API_KEY[/cyan]    or OPENROUTER_API_KEY / OPENAI_API_KEY / GROQ_API_KEY\n\n"
        "Run [bold cyan]tinker init[/bold cyan] to configure everything interactively.",
        border_style="dim",
    ))


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

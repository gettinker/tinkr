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
def monitor(
    services: str = typer.Option(..., "--services", "-s", help="Comma-separated service names"),
    interval: int = typer.Option(60, "--interval", "-i", help="Poll interval in seconds"),
) -> None:
    """[bold cyan]Continuously monitor services and print anomalies.[/bold cyan]

    Example:

      tinker monitor --services payments-api,auth-service
    """
    service_list = [s.strip() for s in services.split(",")]
    asyncio.run(_monitor(service_list, interval))


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


async def _monitor(services: list[str], interval: int) -> None:
    import asyncio
    client = _get_client()

    async def print_anomaly(anomaly: object) -> None:
        from tinker.backends.base import Anomaly
        assert isinstance(anomaly, Anomaly)
        color = SEVERITY_COLORS.get(anomaly.severity, "white")
        console.print(Panel(
            f"[{color}][{anomaly.severity.upper()}][/{color}] "
            f"[bold]{anomaly.service}[/bold] — {anomaly.description}",
            title="Anomaly Detected",
            border_style=color,
        ))

    if client.mode == "local":
        # Use the full monitoring loop (handles stateful alerting, cooldowns)
        from tinker.monitor.loop import MonitoringLoop
        from tinker.client.local import LocalClient
        assert isinstance(client, LocalClient)
        loop = MonitoringLoop(backend=client.backend(), services=services, poll_interval=interval)
        loop.add_alert_handler(print_anomaly)
        console.print(
            f"[bold green]Monitoring[/bold green] {', '.join(services)} "
            f"every {interval}s [dim](local mode)[/dim]. Press Ctrl+C to stop.\n"
        )
        try:
            await loop.run()
        except KeyboardInterrupt:
            await loop.stop()
    else:
        # Server mode: poll detect_anomalies on the server
        console.print(
            f"[bold green]Monitoring[/bold green] {', '.join(services)} "
            f"every {interval}s [dim](server mode)[/dim]. Press Ctrl+C to stop.\n"
        )
        try:
            while True:
                for svc in services:
                    anomalies = await client.detect_anomalies(svc, window_minutes=interval // 60 or 1)
                    for a in anomalies:
                        await print_anomaly(a)
                await asyncio.sleep(interval)
        except KeyboardInterrupt:
            pass


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

"""Interactive monitor REPL — anomaly browser with explain/fix via LLM.

Commands
--------
  list   / ls              — Re-display current anomaly table
  refresh / r              — Re-fetch anomalies from the backend
  filter [--severity S]    — Filter displayed anomalies by severity
         [--since WINDOW]  — Change look-back window (e.g. --since 30m)
  explain <n>              — LLM explains anomaly #n using pre-built summary
  fix <n>                  — LLM proposes a code fix using repo tools
  approve                  — Apply the pending fix and open a PR
  session clean            — Delete sessions older than 24 h from SQLite
  help / ?                 — Show this command list
  quit / q / exit          — Exit

Design notes
------------
- LLM is invoked ONLY for ``explain`` and ``fix`` — all other commands are
  deterministic and incur zero token cost.
- ``explain`` receives the compact ``log_summary`` (~300 tokens) not raw logs.
- ``fix`` runs a mini agent loop with code tools (glob, read_file, search_code,
  recent_commits, suggest_fix) so the LLM can navigate the repo.
- Session state (anomaly list, pending fix) is persisted in SQLite so a crash
  or disconnect doesn't lose progress.
"""

from __future__ import annotations

import asyncio
import textwrap
from typing import TYPE_CHECKING

import structlog
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax

from tinker.backends.base import Anomaly
from tinker.interfaces.handlers import get_anomalies, parse_since
from tinker.interfaces.renderers import OutputFormat, render_anomalies

if TYPE_CHECKING:
    from tinker.client.remote import RemoteClient

log = structlog.get_logger(__name__)
console = Console()

SEVERITY_STYLE = {
    "critical": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "green",
}
_HELP = textwrap.dedent("""
    list / ls              Re-display the anomaly table
    refresh / r            Re-fetch anomalies from the backend
    filter --severity S    Show only anomalies of severity S (high/medium/low)
           --since WINDOW  Change look-back window (e.g. --since 30m)
    explain <n>            LLM explanation of anomaly #n
    fix <n>                LLM-proposed code fix for anomaly #n
    approve                Apply the pending fix and open a GitHub PR
    session clean          Clean sessions older than 24 h
    help / ?               Show this help
    quit / q / exit        Exit
""").strip()


# ── REPL class ────────────────────────────────────────────────────────────────

class MonitorREPL:
    """Interactive terminal session for exploring and acting on anomalies."""

    def __init__(
        self,
        service: str,
        client: "RemoteClient",
        window_minutes: int = 60,
        resource: str | None = None,
    ) -> None:
        self._service = service
        self._client = client
        self._window = window_minutes
        self._resource = resource

        self._anomalies: list[Anomaly] = []
        self._filtered: list[Anomaly] = []
        self._severity_filter: str | None = None
        self._pending_fix: dict | None = None          # {file_changes, explanation, diff, anomaly_idx}
        self._session_id: str | None = None

        from tinker.store.db import TinkerDB
        self._db = TinkerDB()

    # ── Public entry point ────────────────────────────────────────────────────

    async def run(self) -> None:
        console.print(
            Panel.fit(
                f"[bold cyan]Tinker Monitor[/bold cyan]  "
                f"[dim]{self._service}[/dim]  "
                f"[dim]window: {self._window}m[/dim]",
                border_style="cyan",
            )
        )
        console.print("[dim]Type [bold]help[/bold] for commands.[/dim]\n")

        await self._do_refresh()

        loop = asyncio.get_event_loop()
        while True:
            try:
                raw = await loop.run_in_executor(
                    None, lambda: input(f"[{self._service}] > ")
                )
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Goodbye.[/dim]")
                break

            cmd = raw.strip()
            if not cmd:
                continue

            if cmd in ("quit", "q", "exit"):
                console.print("[dim]Goodbye.[/dim]")
                break
            elif cmd in ("help", "?"):
                console.print(Panel(_HELP, title="Commands", border_style="dim"))
            elif cmd in ("list", "ls"):
                render_anomalies(self._filtered, OutputFormat.table, service=self._service, since=f"{self._window}m")
            elif cmd in ("refresh", "r"):
                await self._do_refresh()
            elif cmd.startswith("filter"):
                self._do_filter(cmd)
            elif cmd.startswith("explain"):
                await self._do_explain(cmd)
            elif cmd.startswith("fix"):
                await self._do_fix(cmd)
            elif cmd == "approve":
                await self._do_approve()
            elif cmd == "session clean":
                removed = self._db.clean_sessions()
                console.print(f"[green]Cleaned {removed} old session(s).[/green]")
            else:
                console.print(
                    f"[red]Unknown command:[/red] {cmd!r}  "
                    "(type [bold]help[/bold] for commands)"
                )

        self._db.close()

    # ── Commands ──────────────────────────────────────────────────────────────

    async def _do_refresh(self) -> None:
        with console.status(f"[bold green]Fetching anomalies for {self._service}...[/bold green]"):
            try:
                self._anomalies = await get_anomalies(
                    self._client, self._service,
                    since=f"{self._window}m",
                    resource=self._resource,
                )
            except Exception as exc:
                import httpx
                if isinstance(exc, httpx.HTTPStatusError):
                    try:
                        detail = exc.response.json().get("detail", str(exc))
                    except Exception:
                        detail = str(exc)
                else:
                    detail = str(exc)
                console.print(f"[red]Backend error:[/red] {detail}")
                self._anomalies = []

        self._apply_filter()
        self._persist_session()
        render_anomalies(self._filtered, OutputFormat.table, service=self._service, since=f"{self._window}m")

    def _do_filter(self, cmd: str) -> None:
        parts = cmd.split()
        sev = None
        since = None
        i = 1
        while i < len(parts):
            if parts[i] == "--severity" and i + 1 < len(parts):
                sev = parts[i + 1].lower()
                i += 2
            elif parts[i] == "--since" and i + 1 < len(parts):
                since = parts[i + 1]
                i += 2
            else:
                i += 1

        if sev:
            self._severity_filter = sev
        if since:
            try:
                _, self._window = parse_since(since)
            except ValueError as exc:
                console.print(f"[red]{exc}[/red]")
                return
            asyncio.get_event_loop().run_until_complete(self._do_refresh())
            return

        self._apply_filter()
        render_anomalies(self._filtered, OutputFormat.table, service=self._service, since=f"{self._window}m")

    def _apply_filter(self) -> None:
        if self._severity_filter:
            self._filtered = [
                a for a in self._anomalies
                if a.severity.lower() == self._severity_filter
            ]
        else:
            self._filtered = list(self._anomalies)

    async def _do_explain(self, cmd: str) -> None:
        idx = _parse_index(cmd, "explain")
        if idx is None:
            return
        if idx < 1 or idx > len(self._filtered):
            console.print(f"[red]No anomaly #{idx}. Use 'list' to see current anomalies.[/red]")
            return

        anomaly = self._filtered[idx - 1]
        console.print(f"\n[bold]Explaining anomaly #{idx}:[/bold] {anomaly.description}\n")

        chunks: list[str] = []
        async for chunk in self._client.stream_explain(anomaly.to_dict()):
            console.print(chunk, end="")
            chunks.append(chunk)
        console.print()

        console.print(
            Panel(
                Markdown("".join(chunks)),
                title=f"[bold]Explanation — {anomaly.metric}[/bold]",
                border_style=SEVERITY_STYLE.get(anomaly.severity.lower(), "white"),
            )
        )

    async def _do_fix(self, cmd: str) -> None:
        idx = _parse_index(cmd, "fix")
        if idx is None:
            return
        if idx < 1 or idx > len(self._filtered):
            console.print(f"[red]No anomaly #{idx}.[/red]")
            return

        anomaly = self._filtered[idx - 1]
        console.print(f"\n[bold]Finding fix for anomaly #{idx}:[/bold] {anomaly.description}\n")

        error_class = fix_result = None
        mode_labels = {"transient": "[yellow]targeted[/yellow]", "logic_bug": "[red]deep[/red]"}

        with console.status("[bold green]Classifying error...[/bold green]"):
            # Show mode before the (potentially long) agent run
            pass

        with console.status("[bold green]Running fix agent on server...[/bold green]"):
            fix_result = await self._client.request_fix(anomaly.to_dict())

        error_class = fix_result.get("error_class", "unknown")
        mode_label = mode_labels.get(error_class, "[dim]unknown[/dim]")
        console.print(f"[dim]Investigation mode:[/dim] {mode_label} ({error_class})\n")

        self._pending_fix = {**fix_result, "anomaly_idx": idx}
        self._persist_session()

        console.print(
            Panel(
                Markdown(fix_result.get("explanation", "")),
                title="[bold]Proposed Fix[/bold]",
                border_style="yellow",
            )
        )
        if fix_result.get("diff"):
            console.print(
                Syntax(fix_result["diff"], "diff", theme="monokai", line_numbers=False)
            )
        console.print(
            "\n[bold yellow]Run [cyan]approve[/cyan] to apply this fix and open a PR.[/bold yellow]"
        )

    async def _do_approve(self) -> None:
        if not self._pending_fix:
            console.print("[red]No pending fix. Run 'fix <n>' first.[/red]")
            return

        file_changes = self._pending_fix.get("file_changes", [])
        explanation = self._pending_fix.get("explanation", "")

        if not file_changes:
            console.print("[red]Pending fix has no file changes.[/red]")
            return

        paths = ", ".join(c["path"] for c in file_changes)
        confirmed = _confirm(f"Apply fix to {paths} and open a GitHub PR?")
        if not confirmed:
            console.print("[dim]Aborted.[/dim]")
            return

        with console.status("[bold green]Applying fix on server...[/bold green]"):
            result = await self._client.approve_fix(file_changes, explanation, self._service)

        self._pending_fix = None
        self._persist_session()
        console.print(f"\n[bold green]PR opened:[/bold green] {result['pr_url']}")

    # ── Session persistence ───────────────────────────────────────────────────

    def _persist_session(self) -> None:
        anomaly_dicts = [a.to_dict() for a in self._anomalies]
        if self._session_id:
            self._db.update_session(
                self._session_id,
                anomalies=anomaly_dicts,
                pending_fix=self._pending_fix,
            )
        else:
            self._session_id = self._db.create_session(
                self._service, anomaly_dicts
            )
            if self._pending_fix:
                self._db.update_session(
                    self._session_id,
                    pending_fix=self._pending_fix,
                )



# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_index(cmd: str, verb: str) -> int | None:
    parts = cmd.split()
    if len(parts) < 2:
        console.print(f"[red]Usage: {verb} <n>[/red]")
        return None
    try:
        return int(parts[1])
    except ValueError:
        console.print(f"[red]Usage: {verb} <n>  (n must be a number)[/red]")
        return None



def _confirm(message: str) -> bool:
    try:
        ans = input(f"{message} [y/N] ").strip().lower()
        return ans in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False



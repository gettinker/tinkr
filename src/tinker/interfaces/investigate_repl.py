"""tinker investigate — log-driven error investigation REPL.

Two-level interactive session:

  Level 1 — Error groups (default view)
  ──────────────────────────────────────
  Fetches ERROR/WARN logs, runs the LogSummarizer pattern normaliser, and
  shows grouped results ranked by occurrence count.

  Level 2 — Log drill-down  (after `logs <n>`)
  ──────────────────────────────────────────────
  Shows individual log entries for the selected group. Supports scrolling
  and per-entry explain.

Commands
--------
  list / ls              — Re-display the error groups table
  refresh / r            — Re-fetch logs and regroup
  filter --since WINDOW  — Change look-back window (re-fetches)
  filter --level L       — Show only groups at level ERROR|WARN|ALL
  logs <n>               — Drill into group #n — show individual entries
  back / b               — Return to groups view (from drill-down)
  explain [n]            — AI explains group #n (or the current drill-down group)
  fix [n]                — AI proposes a code fix for group #n
  approve                — Apply the pending fix and open a GitHub PR
  session clean          — Delete sessions older than 24 h from SQLite
  help / ?               — Show this command list
  quit / q / exit        — Exit

Design notes
------------
- LLM is invoked ONLY for explain and fix — all other commands are
  deterministic and incur zero token cost.
- explain shows error classification (transient / logic_bug / config_error /
  dependency_down) before the AI narrative so the user knows what to expect.
- fix skips patch generation for transient errors and says why.
- Session state (groups, pending fix) is persisted in SQLite so a crash does
  not lose progress.
"""

from __future__ import annotations

import asyncio
import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

import structlog
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from tinker.interfaces.handlers import get_logs, parse_since

if TYPE_CHECKING:
    from tinker.backends.base import LogEntry
    from tinker.client.remote import RemoteClient

log = structlog.get_logger(__name__)
console = Console()

_LEVEL_STYLE: dict[str, str] = {
    "ERROR":    "red",
    "CRITICAL": "bold red",
    "WARN":     "yellow",
    "WARNING":  "yellow",
    "INFO":     "green",
    "DEBUG":    "dim",
}

_CLASS_STYLE: dict[str, str] = {
    "transient":        "yellow",
    "logic_bug":        "red",
    "config_error":     "magenta",
    "dependency_down":  "bold red",
    "unknown":          "dim",
}

_HELP = textwrap.dedent("""
    list / ls              Re-display the error groups table
    refresh / r            Re-fetch logs and regroup
    filter --since WINDOW  Change look-back window (e.g. --since 30m)
    filter --level L       Filter to ERROR, WARN or ALL (default: ERROR)
    logs <n>               Drill into group #n — show individual entries
    back / b               Return to groups view
    explain [n]            AI explains group #n (omit n when in drill-down)
    fix [n]                AI-proposed code fix for group #n
    approve                Apply the pending fix and open a GitHub PR
    session clean          Clean sessions older than 24 h
    help / ?               Show this help
    quit / q / exit        Exit
""").strip()


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class ErrorGroup:
    """One deduplicated error pattern and its representative logs."""
    template: str                       # normalised message template
    count: int
    level: str                          # dominant log level
    first_seen: datetime | None
    last_seen: datetime | None
    entries: list[LogEntry] = field(default_factory=list)   # representative logs
    stack_traces: list[dict] = field(default_factory=list)  # from LogSummarizer
    summary: dict = field(default_factory=dict)             # full summary dict


def _build_groups(logs: list[LogEntry], window_minutes: int) -> list[ErrorGroup]:
    """Run LogSummarizer over *logs* and convert into ErrorGroup list."""
    from tinker.agent.summarizer import LogSummarizer
    summarizer = LogSummarizer()
    representative, summary = summarizer.summarize(logs, window_minutes=window_minutes)

    # Build a lookup of template → representative entry
    rep_by_tmpl: dict[str, LogEntry] = {e.message: e for e in representative}

    groups: list[ErrorGroup] = []
    for p in summary.get("unique_patterns", []):
        tmpl = p["template"]
        rep_entry = rep_by_tmpl.get(p.get("example", ""))

        # Find all entries matching this template to determine level / timestamps
        matching = [
            e for e in logs
            if _normalize_msg(e.message) == tmpl
        ]
        levels = [e.level.upper() for e in matching if e.level]
        dominant_level = max(set(levels), key=levels.count) if levels else "ERROR"
        timestamps = sorted(e.timestamp for e in matching if e.timestamp)

        groups.append(ErrorGroup(
            template=tmpl,
            count=p["count"],
            level=dominant_level,
            first_seen=timestamps[0] if timestamps else None,
            last_seen=timestamps[-1] if timestamps else None,
            entries=matching[:50],   # keep up to 50 raw entries for drill-down
            stack_traces=summary.get("stack_traces", []),
            summary=summary,
        ))

    return sorted(groups, key=lambda g: g.count, reverse=True)


def _normalize_msg(msg: str) -> str:
    from tinker.agent.summarizer import _normalize_message
    return _normalize_message(msg or "")


# ── REPL class ────────────────────────────────────────────────────────────────

class InvestigateREPL:
    """Interactive two-level log investigation session."""

    def __init__(
        self,
        service: str,
        client: "RemoteClient",
        window_minutes: int = 30,
        level: str = "ERROR",
        resource: str | None = None,
    ) -> None:
        self._service = service
        self._client = client
        self._window = window_minutes
        self._level = level.upper()
        self._resource = resource

        self._groups: list[ErrorGroup] = []
        self._drill_group: ErrorGroup | None = None   # set when in drill-down mode
        self._pending_fix: dict | None = None
        self._session_id: str | None = None

        from tinker.store.db import TinkerDB
        self._db = TinkerDB()

    # ── Public entry point ────────────────────────────────────────────────────

    async def run(self) -> None:
        console.print(
            Panel.fit(
                f"[bold cyan]Tinker Investigate[/bold cyan]  "
                f"[dim]{self._service}[/dim]  "
                f"[dim]window: {self._window}m  level: {self._level}[/dim]",
                border_style="cyan",
            )
        )
        console.print("[dim]Type [bold]help[/bold] for commands.[/dim]\n")

        await self._do_refresh()

        loop = asyncio.get_event_loop()
        while True:
            prompt = self._prompt()
            try:
                raw = await loop.run_in_executor(None, lambda: input(prompt))
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Goodbye.[/dim]")
                break

            cmd = raw.strip()
            if not cmd:
                continue

            if cmd in ("quit", "q", "exit"):
                console.print("[dim]Goodbye.[/dim]")
                break
            else:
                try:
                    await self._dispatch(cmd)
                except Exception as exc:
                    console.print(f"[red]Error:[/red] {exc}")
                    log.exception("investigate_repl.command_error", cmd=cmd)

        self._db.close()

    async def _dispatch(self, cmd: str) -> None:
        if cmd in ("help", "?"):
            console.print(Panel(_HELP, title="Commands", border_style="dim"))
        elif cmd in ("list", "ls"):
            if self._drill_group:
                self._print_entries(self._drill_group)
            else:
                self._print_groups()
        elif cmd in ("back", "b"):
            self._drill_group = None
            self._print_groups()
        elif cmd in ("refresh", "r"):
            await self._do_refresh()
        elif cmd.startswith("filter"):
            await self._do_filter(cmd)
        elif cmd.startswith("logs"):
            self._do_logs(cmd)
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

    def _prompt(self) -> str:
        if self._drill_group:
            short = self._drill_group.template[:30].replace("\n", " ")
            return f"[{self._service} | {short}…] > "
        return f"[{self._service}] > "

    # ── Commands ──────────────────────────────────────────────────────────────

    async def _do_refresh(self) -> None:
        query = f"level:{self._level}" if self._level != "ALL" else "*"
        with console.status(f"[bold green]Fetching logs for {self._service}...[/bold green]"):
            try:
                logs = await get_logs(
                    self._client, self._service,
                    query=query,
                    since=f"{self._window}m",
                    limit=500,
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
                self._groups = []
                return

        with console.status("[bold green]Grouping error patterns...[/bold green]"):
            self._groups = _build_groups(logs, self._window)

        self._drill_group = None
        self._persist_session()
        self._print_groups()

    async def _do_filter(self, cmd: str) -> None:
        parts = cmd.split()
        since = None
        level = None
        i = 1
        while i < len(parts):
            if parts[i] == "--since" and i + 1 < len(parts):
                since = parts[i + 1]
                i += 2
            elif parts[i] == "--level" and i + 1 < len(parts):
                level = parts[i + 1].upper()
                i += 2
            else:
                i += 1

        changed = False
        if since:
            try:
                _, self._window = parse_since(since)
                changed = True
            except ValueError as exc:
                console.print(f"[red]{exc}[/red]")
                return
        if level:
            if level not in ("ERROR", "WARN", "WARNING", "ALL"):
                console.print("[red]--level must be ERROR, WARN, or ALL[/red]")
                return
            self._level = level
            changed = True

        if changed:
            await self._do_refresh()
        else:
            console.print("[dim]No filter changed.[/dim]")

    def _do_logs(self, cmd: str) -> None:
        idx = _parse_index(cmd, "logs")
        if idx is None:
            return
        if idx < 1 or idx > len(self._groups):
            console.print(f"[red]No group #{idx}. Use 'list' to see groups.[/red]")
            return
        self._drill_group = self._groups[idx - 1]
        self._print_entries(self._drill_group)

    async def _do_explain(self, cmd: str) -> None:
        group = self._resolve_group(cmd, "explain")
        if group is None:
            return

        console.print(f"\n[bold]Explaining:[/bold] {group.template[:80]}\n")

        # Build an anomaly-like dict the server's explain endpoint understands
        anomaly_dict = _group_to_anomaly_dict(group, self._service)

        chunks: list[str] = []
        with console.status("[bold green]Generating explanation...[/bold green]"):
            async for chunk in self._client.stream_explain(anomaly_dict):
                chunks.append(chunk)

        full_text = "".join(chunks)
        error_class = _extract_class(full_text)
        border = _CLASS_STYLE.get(error_class or "", "cyan")

        console.print(
            Panel(
                Markdown(full_text),
                title=f"[bold]Explanation — {group.template[:60]}[/bold]",
                border_style=border,
            )
        )

        if error_class == "transient":
            console.print(
                "[dim]Transient error — no code fix needed. "
                "Check infra, retries, or circuit-breaker config.[/dim]"
            )
        else:
            console.print(
                "[dim]Run [bold]fix[/bold] to get a proposed code patch.[/dim]"
            )

    async def _do_fix(self, cmd: str) -> None:
        group = self._resolve_group(cmd, "fix")
        if group is None:
            return

        console.print(f"\n[bold]Finding fix for:[/bold] {group.template[:80]}\n")

        anomaly_dict = _group_to_anomaly_dict(group, self._service)

        with console.status("[bold green]Running fix agent on server...[/bold green]"):
            fix_result = await self._client.request_fix(anomaly_dict)

        error_class = fix_result.get("error_class", "unknown")
        style = _CLASS_STYLE.get(error_class, "dim")
        console.print(f"[dim]Error class:[/dim] [{style}]{error_class}[/{style}]\n")

        has_changes = bool(fix_result.get("file_changes") or fix_result.get("diff"))

        if error_class == "transient" and not has_changes:
            console.print(
                Panel(
                    fix_result.get("explanation", "Transient error — no patch generated."),
                    title="[bold]Analysis[/bold]",
                    border_style="yellow",
                )
            )
            return

        self._pending_fix = {**fix_result, "group_template": group.template}
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
        if not _confirm(f"Apply fix to {paths} and open a GitHub PR?"):
            console.print("[dim]Aborted.[/dim]")
            return

        try:
            with console.status("[bold green]Applying fix on server...[/bold green]"):
                result = await self._client.approve_fix(file_changes, explanation, self._service)
        except Exception as exc:
            console.print(f"[red]Server error:[/red] {exc}")
            return

        self._pending_fix = None
        self._persist_session()
        console.print(f"\n[bold green]PR opened:[/bold green] {result['pr_url']}")

    # ── Resolvers ─────────────────────────────────────────────────────────────

    def _resolve_group(self, cmd: str, verb: str) -> ErrorGroup | None:
        """Return the target ErrorGroup for explain/fix.

        If in drill-down mode and no index given, uses the current group.
        Otherwise parses <n> from the command.
        """
        parts = cmd.split()
        if len(parts) == 1:
            # No index — use drill-down group if available
            if self._drill_group:
                return self._drill_group
            console.print(f"[red]Usage: {verb} <n>  (or drill into a group with 'logs <n>' first)[/red]")
            return None

        idx = _parse_index(cmd, verb)
        if idx is None:
            return None
        if idx < 1 or idx > len(self._groups):
            console.print(f"[red]No group #{idx}. Use 'list' to see groups.[/red]")
            return None
        return self._groups[idx - 1]

    # ── Renderers ─────────────────────────────────────────────────────────────

    def _print_groups(self) -> None:
        if not self._groups:
            console.print(
                f"[dim]No {self._level} entries found for {self._service} "
                f"in the last {self._window}m.[/dim]"
            )
            return

        table = Table(
            show_header=True,
            header_style="bold magenta",
            title=f"Error Groups — {self._service} (last {self._window}m, level={self._level})",
            show_lines=True,
        )
        table.add_column("#", width=3, justify="right", no_wrap=True)
        table.add_column("Level", width=8, no_wrap=True)
        table.add_column("Count", width=7, justify="right", no_wrap=True)
        table.add_column("Pattern", ratio=1, overflow="fold")
        table.add_column("Traces", width=7, justify="right", no_wrap=True)
        table.add_column("First seen", width=10, no_wrap=True)

        for i, g in enumerate(self._groups, 1):
            lvl_style = _LEVEL_STYLE.get(g.level, "white")
            n_traces = len(g.stack_traces)
            first = g.first_seen.strftime("%H:%M:%S") if g.first_seen else "—"
            table.add_row(
                str(i),
                f"[{lvl_style}]{g.level}[/{lvl_style}]",
                str(g.count),
                g.template,
                f"[red]{n_traces}[/red]" if n_traces else "—",
                first,
            )

        console.print(table)
        console.print(
            "[dim]Commands: logs <n> · explain <n> · fix <n> · filter --since 30m · refresh[/dim]"
        )

    def _print_entries(self, group: ErrorGroup) -> None:
        console.print(
            Panel(
                f"[bold]{group.template}[/bold]\n\n"
                f"[dim]{group.count} occurrences · {group.level} · "
                f"first={group.first_seen.strftime('%H:%M:%S') if group.first_seen else '?'} "
                f"last={group.last_seen.strftime('%H:%M:%S') if group.last_seen else '?'}[/dim]",
                title="Group detail",
                border_style="cyan",
            )
        )

        if group.stack_traces:
            for t in group.stack_traces[:2]:
                console.print(
                    Panel(
                        t.get("full_trace", "")[:600],
                        title=f"[red]Stack trace ({t.get('language','?')} · {t.get('count',0)}×)[/red]",
                        border_style="red",
                    )
                )

        table = Table(show_header=True, header_style="bold magenta", title="Log entries",
                      show_lines=True)
        table.add_column("#", width=3, justify="right", no_wrap=True)
        table.add_column("Time", width=10, no_wrap=True)
        table.add_column("Level", width=8, no_wrap=True)
        table.add_column("Message", overflow="fold")

        entries = group.entries[:30]
        for i, e in enumerate(entries, 1):
            lvl_style = _LEVEL_STYLE.get((e.level or "").upper(), "white")
            ts = e.timestamp.strftime("%H:%M:%S") if e.timestamp else "?"
            table.add_row(
                str(i),
                ts,
                f"[{lvl_style}]{e.level}[/{lvl_style}]",
                e.message or "",
            )

        console.print(table)
        if len(group.entries) > 30:
            console.print(f"[dim]Showing 30 of {len(group.entries)} entries.[/dim]")
        console.print("[dim]Commands: explain · fix · back[/dim]")

    # ── Session persistence ───────────────────────────────────────────────────

    def _persist_session(self) -> None:
        group_dicts = [_group_to_anomaly_dict(g, self._service) for g in self._groups]
        if self._session_id:
            self._db.update_session(
                self._session_id,
                anomalies=group_dicts,
                pending_fix=self._pending_fix,
            )
        else:
            self._session_id = self._db.create_session(self._service, group_dicts)
            if self._pending_fix:
                self._db.update_session(self._session_id, pending_fix=self._pending_fix)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _group_to_anomaly_dict(group: ErrorGroup, service: str) -> dict:
    """Convert an ErrorGroup into the anomaly dict shape the server endpoints expect."""
    return {
        "service": service,
        "metric": "error_logs",
        "severity": "high" if group.level in ("ERROR", "CRITICAL") else "medium",
        "description": group.template[:200],
        "current_value": float(group.count),
        "threshold": 0.0,
        "detected_at": group.first_seen.isoformat() if group.first_seen else "",
        "log_summary": {
            **group.summary,
            "unique_patterns": [
                {"template": group.template, "count": group.count,
                 "example": group.entries[0].message if group.entries else ""}
            ],
            "stack_traces": group.stack_traces,
        },
    }


def _extract_class(text: str) -> str | None:
    """Try to extract an error classification tag from the LLM response."""
    import re
    m = re.search(
        r'\b(transient|logic_bug|config_error|dependency_down)\b',
        text, re.I
    )
    return m.group(1).lower() if m else None


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

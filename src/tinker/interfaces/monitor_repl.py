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
import shutil
import textwrap
from datetime import timezone
from typing import TYPE_CHECKING

import structlog
from rich.columns import Columns
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from tinker.backends.base import Anomaly
from tinker.monitor.summarizer import build_explain_context

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
        model: str | None = None,
        repo_path: str | None = None,
    ) -> None:
        self._service = service
        self._client = client
        self._window = window_minutes
        self._model = model or _default_model()
        self._repo_path = repo_path or _find_repo()

        self._anomalies: list[Anomaly] = []
        self._filtered: list[Anomaly] = []
        self._severity_filter: str | None = None
        self._pending_fix: dict | None = None          # {diff, explanation, anomaly_idx}
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
                self._print_table()
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
                self._anomalies = await self._client.detect_anomalies(
                    self._service, window_minutes=self._window
                )
            except Exception as exc:
                console.print(f"[red]Backend error:[/red] {exc}")
                self._anomalies = []

        self._apply_filter()
        self._persist_session()
        self._print_table()

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
            # Parse window
            try:
                unit = since[-1]
                val = int(since[:-1])
                match unit:
                    case "m": self._window = val
                    case "h": self._window = val * 60
                    case "d": self._window = val * 1440
                    case _:
                        console.print("[red]Unknown time unit. Use m/h/d.[/red]")
                        return
                # Re-fetch with new window
                asyncio.get_event_loop().run_until_complete(self._do_refresh())
                return
            except (ValueError, IndexError):
                console.print("[red]Invalid --since value.[/red]")
                return

        self._apply_filter()
        self._print_table()

    def _apply_filter(self) -> None:
        if self._severity_filter:
            self._filtered = [
                a for a in self._anomalies
                if a.severity.lower() == self._severity_filter
            ]
        else:
            self._filtered = list(self._anomalies)

    def _print_table(self) -> None:
        if not self._filtered:
            if self._severity_filter:
                console.print(
                    f"[dim]No {self._severity_filter} anomalies in the last {self._window}m.[/dim]"
                )
            else:
                console.print(
                    f"[dim]No anomalies detected in the last {self._window}m.[/dim]"
                )
            return

        table = Table(
            show_header=True,
            header_style="bold magenta",
            title=f"Anomalies — {self._service} (last {self._window}m)",
        )
        table.add_column("#", width=3, justify="right")
        table.add_column("Severity", width=9)
        table.add_column("Metric", width=20)
        table.add_column("Description")
        table.add_column("Patterns", width=8, justify="right")
        table.add_column("Traces", width=7, justify="right")

        for i, a in enumerate(self._filtered, 1):
            sev_style = SEVERITY_STYLE.get(a.severity.lower(), "white")
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
            "[dim]Commands: explain <n> · fix <n> · filter --severity high · refresh[/dim]"
        )

    async def _do_explain(self, cmd: str) -> None:
        idx = _parse_index(cmd, "explain")
        if idx is None:
            return
        if idx < 1 or idx > len(self._filtered):
            console.print(f"[red]No anomaly #{idx}. Use 'list' to see current anomalies.[/red]")
            return

        anomaly = self._filtered[idx - 1]
        context_block = build_explain_context(anomaly.to_dict())

        prompt = (
            "You are an expert SRE analysing a production anomaly. "
            "Based on the structured summary below, explain:\n"
            "1. What is happening (root cause hypothesis)\n"
            "2. Why it is happening (likely trigger)\n"
            "3. Immediate impact\n"
            "4. What to look at next\n\n"
            "Be concise. Focus on actionable insight, not restatement of the data.\n\n"
            "--- Anomaly Summary ---\n"
            f"{context_block}\n"
            "--- End Summary ---"
        )

        console.print(f"\n[bold]Explaining anomaly #{idx}:[/bold] {anomaly.description}\n")
        with console.status("[bold green]Asking LLM...[/bold green]"):
            explanation = await _llm_complete(prompt, self._model)

        console.print(
            Panel(
                Markdown(explanation),
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

        # Ensure we have a repo
        repo_path = self._repo_path
        if not repo_path:
            repo_path = _prompt_repo_path()
            if not repo_path:
                return
            self._repo_path = repo_path

        context_block = build_explain_context(anomaly.to_dict())

        system_prompt = (
            "You are an expert SRE and software engineer. "
            "Given an anomaly summary and access to the service's codebase, "
            "find the root cause and propose a minimal, safe fix as a unified diff.\n\n"
            "Use the available tools to:\n"
            "1. Search for relevant code (search_code, glob_files)\n"
            "2. Read relevant files (get_file)\n"
            "3. Check recent commits (get_recent_commits)\n"
            "4. Propose the fix (suggest_fix) — include a unified diff and explanation\n\n"
            "Focus on the stack trace file paths and exception types first.\n"
            "Do NOT apply the fix — just suggest it.\n\n"
            "--- Anomaly Summary ---\n"
            f"{context_block}\n"
            "--- End Summary ---"
        )

        console.print(f"\n[bold]Finding fix for anomaly #{idx}:[/bold] {anomaly.description}")
        console.print(f"[dim]Repo: {repo_path}[/dim]\n")

        fix_result = await _run_fix_agent(
            system_prompt=system_prompt,
            model=self._model,
            repo_path=repo_path,
            service=self._service,
            anomaly=anomaly,
        )

        if fix_result:
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
                    Syntax(
                        fix_result["diff"],
                        "diff",
                        theme="monokai",
                        line_numbers=False,
                    )
                )
            console.print(
                "\n[bold yellow]Run [cyan]approve[/cyan] to apply this fix and open a PR.[/bold yellow]"
            )
        else:
            console.print("[dim]No fix proposed.[/dim]")

    async def _do_approve(self) -> None:
        if not self._pending_fix:
            console.print("[red]No pending fix. Run 'fix <n>' first.[/red]")
            return

        diff = self._pending_fix.get("diff", "")
        explanation = self._pending_fix.get("explanation", "")

        if not diff:
            console.print("[red]Pending fix has no diff.[/red]")
            return

        confirmed = _confirm(
            f"Apply fix and open a GitHub PR? "
            f"(requires GITHUB_TOKEN + GITHUB_REPO configured)"
        )
        if not confirmed:
            console.print("[dim]Aborted.[/dim]")
            return

        # Check GitHub config
        from tinker.config import settings
        if not settings.github_token or not settings.github_repo:
            console.print(
                "[red]GITHUB_TOKEN and GITHUB_REPO are not configured.[/red]\n"
                "[dim]Set them in your .env or tinker.toml, then run approve again.[/dim]"
            )
            return

        from tinker.code.fix_applier import FixApplier
        import uuid
        branch = f"tinker/fix-{uuid.uuid4().hex[:8]}"

        with console.status("[bold green]Validating and applying fix...[/bold green]"):
            try:
                applier = FixApplier(repo_path=self._repo_path or ".")
                pr_url = await applier.create_pr(
                    diff=diff,
                    branch_name=branch,
                    title=f"fix: tinker auto-fix for {self._service}",
                    body=explanation,
                )
                self._pending_fix = None
                self._persist_session()
                console.print(f"\n[bold green]PR opened:[/bold green] {pr_url}")
            except Exception as exc:
                console.print(f"[red]Failed to create PR:[/red] {exc}")

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


# ── Fix agent loop ────────────────────────────────────────────────────────────

async def _run_fix_agent(
    system_prompt: str,
    model: str,
    repo_path: str,
    service: str,
    anomaly: Anomaly,
) -> dict | None:
    """Mini agent loop: LLM + code tools → returns {diff, explanation} or None."""
    from tinker.agent import llm as llm_mod
    from tinker.code.repo import RepoClient

    repo = RepoClient(repo_path)

    # Tool definitions for fix agent
    tools = [
        _fn("glob_files", "Find files by glob pattern in the repo.",
            {"type": "object", "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, e.g. **/*.py"},
                "max_results": {"type": "integer", "default": 20},
            }, "required": ["pattern"]}),
        _fn("get_file", "Read a source file from the repo.",
            {"type": "object", "properties": {
                "path": {"type": "string", "description": "File path relative to repo root"},
            }, "required": ["path"]}),
        _fn("search_code", "Search the codebase for a pattern (ripgrep).",
            {"type": "object", "properties": {
                "pattern": {"type": "string"},
                "file_glob": {"type": "string", "default": "**/*.py"},
                "context_lines": {"type": "integer", "default": 5},
            }, "required": ["pattern"]}),
        _fn("get_recent_commits", "List recent git commits touching a path.",
            {"type": "object", "properties": {
                "path": {"type": "string", "default": "."},
                "n": {"type": "integer", "default": 10},
            }}),
        _fn("suggest_fix", "Propose a unified diff fix. Does NOT apply it.",
            {"type": "object", "properties": {
                "diff": {"type": "string", "description": "Unified diff (--- a/ +++ b/)"},
                "explanation": {"type": "string"},
            }, "required": ["diff", "explanation"]}),
    ]

    messages = [{"role": "user", "content": system_prompt}]
    result: dict | None = None
    MAX_TURNS = 10

    for turn in range(MAX_TURNS):
        with console.status(f"[dim]Agent turn {turn + 1}/{MAX_TURNS}...[/dim]"):
            response = llm_mod.complete(messages, model=model, tools=tools, max_tokens=4096)

        if llm_mod.is_tool_call(response):
            messages.append(llm_mod.assistant_message_from_response(response))
            for tc in llm_mod.extract_tool_calls(response):
                tool_result = _dispatch_fix_tool(tc["name"], tc["arguments"], repo)
                if tc["name"] == "suggest_fix":
                    result = tc["arguments"]
                messages.append(
                    llm_mod.tool_result_message(tc["id"], tool_result)
                )
            if result:
                break
        else:
            # Text response — done
            break

    return result


def _dispatch_fix_tool(name: str, args: dict, repo) -> str:
    """Execute a fix-agent tool call and return string result."""
    import glob as glob_mod
    import os

    match name:
        case "glob_files":
            pattern = args.get("pattern", "**/*")
            max_r = args.get("max_results", 20)
            matches = glob_mod.glob(
                os.path.join(str(repo._root), pattern), recursive=True
            )
            # Strip repo root prefix for readability
            root = str(repo._root)
            rel = [m[len(root) + 1:] for m in matches if not _is_binary(m)]
            return "\n".join(rel[:max_r]) or "(no matches)"

        case "get_file":
            return repo.read_file(args["path"])

        case "search_code":
            return repo.search(
                args["pattern"],
                glob=args.get("file_glob", "**/*.py"),
                context_lines=args.get("context_lines", 5),
            )

        case "get_recent_commits":
            commits = repo.recent_commits(
                service_path=args.get("path", "."),
                n=args.get("n", 10),
            )
            return "\n".join(
                f"{c['sha'][:8]} {c['date'][:10]} {c['author']} — {c['subject']}"
                for c in commits
            ) or "(no commits)"

        case "suggest_fix":
            return "Fix staged. Awaiting approval."

        case _:
            return f"Unknown tool: {name}"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fn(name: str, desc: str, params: dict) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": params}}


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


def _default_model() -> str:
    from tinker.config import settings
    return settings.default_model


def _find_repo() -> str | None:
    """Return TINKER_REPO_PATH config, then try current directory."""
    from tinker.config import settings
    if settings.tinker_repo_path:
        return settings.tinker_repo_path
    import subprocess
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def _prompt_repo_path() -> str | None:
    """Ask the user for a repo path interactively."""
    console.print(
        "[yellow]No code repository configured.[/yellow]\n"
        "[dim]Set [bold]TINKER_REPO_PATH[/bold] in your .env to skip this prompt.[/dim]"
    )
    try:
        path = input("Repository path (or Enter to skip): ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not path:
        return None
    import os
    if not os.path.isdir(path):
        console.print(f"[red]Directory not found: {path}[/red]")
        return None
    return path


def _confirm(message: str) -> bool:
    try:
        ans = input(f"{message} [y/N] ").strip().lower()
        return ans in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def _is_binary(path: str) -> bool:
    """Quick check — skip binary files for glob_files results."""
    import os
    BINARY_EXTS = {
        ".pyc", ".so", ".o", ".a", ".dylib", ".dll", ".exe",
        ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".tar", ".gz",
    }
    _, ext = os.path.splitext(path)
    return ext.lower() in BINARY_EXTS


async def _llm_complete(prompt: str, model: str) -> str:
    """Single non-streaming LLM call. Returns text response."""
    import asyncio
    from tinker.agent import llm as llm_mod

    messages = [{"role": "user", "content": prompt}]
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: llm_mod.complete(messages, model=model, max_tokens=2048),
    )
    return llm_mod.extract_text(response)

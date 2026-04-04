"""Agent routes — LLM-powered explain, fix, and approve.

POST /api/v1/explain   — stream LLM explanation, enriched with code context when available
POST /api/v1/fix       — run fix agent; depth driven by error classification
POST /api/v1/approve   — apply staged file changes and open a GitHub PR

Error classification drives investigation depth:
  transient  (DB timeout, network, rate limit, bad input)
    → explain: include only stack trace files from GitHub (no search)
    → fix:     targeted — read call-site only, minimal change, 4 turns max

  logic_bug  (NPE, AttributeError, wrong query, assertion failure)
    → explain: include stack trace files + search for related patterns
    → fix:     deep — full search + commits across repo, 12 turns max

  unknown
    → treat as logic_bug (safer to over-investigate)
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any, AsyncIterator

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from tinker.server.auth import AuthContext, require_auth

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["agent"])

# Lines of context fetched around a stack frame (± N lines)
_CODE_CONTEXT_LINES = 30


# ── Request / response models ─────────────────────────────────────────────────

class ExplainRequest(BaseModel):
    anomaly: dict[str, Any]


class FixRequest(BaseModel):
    anomaly: dict[str, Any]


class FileChange(BaseModel):
    path: str
    new_content: str


class ApproveRequest(BaseModel):
    file_changes: list[FileChange]
    explanation: str
    service: str


# ── SSE helper ────────────────────────────────────────────────────────────────

def _sse(data: str) -> str:
    return f"data: {json.dumps({'text': data})}\n\n"


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/explain")
async def explain(
    req: ExplainRequest,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> StreamingResponse:
    """Stream an LLM explanation, enriched with code context when GitHub is configured."""
    from tinker.agent import llm as llm_mod
    from tinker.agent.error_classifier import classify
    from tinker.config import settings
    from tinker.monitor.summarizer import build_explain_context

    anomaly = req.anomaly
    service = anomaly.get("service", "unknown")

    # Classify to decide how much code context to fetch
    error_class = classify(anomaly)
    log.info("explain.start",
             actor=auth.subject,
             metric=anomaly.get("metric"),
             error_class=error_class.kind,
             confidence=error_class.confidence)

    context_block = build_explain_context(anomaly)
    code_context = _fetch_code_context(error_class, service, deep=error_class.kind == "logic_bug")

    # Build prompt — inject code context if available
    code_section = ""
    if code_context:
        code_section = (
            "\n\n--- Relevant Code (from GitHub) ---\n"
            + code_context +
            "\n--- End Code ---"
        )

    classification_hint = (
        f"\nError classification: {error_class.kind.upper()} "
        f"({error_class.reason})\n"
    )

    prompt = (
        "You are an expert SRE analysing a production anomaly.\n"
        + classification_hint +
        "Based on the anomaly summary"
        + (" and the relevant source code" if code_context else "") +
        " below, explain:\n"
        "1. What is happening (root cause hypothesis)\n"
        "2. Why it is happening (likely trigger)\n"
        "3. Immediate impact\n"
        "4. What to look at next\n\n"
        + ("For TRANSIENT errors: focus on configuration, retries, timeouts, circuit breakers.\n"
           if error_class.kind == "transient" else
           "For LOGIC BUGS: point to the exact file and line number. Explain the code path that leads to the failure.\n")
        + "\nBe concise. Focus on actionable insight.\n\n"
        "--- Anomaly Summary ---\n"
        f"{context_block}"
        f"{code_section}\n"
        "--- End Summary ---"
    )

    async def _stream() -> AsyncIterator[str]:
        # First emit the classification so the REPL can display it
        yield _sse(f"**Error class: {error_class.kind}** — {error_class.reason}\n\n")
        try:
            async for chunk in llm_mod.stream_complete(
                [{"role": "user", "content": prompt}],
                model=settings.default_model,
            ):
                yield _sse(chunk)
        except Exception:
            log.exception("explain.error")
            yield _sse("[Error generating explanation — check server logs]")
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/fix")
async def fix(
    req: FixRequest,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> dict[str, Any]:
    """Run fix agent. Depth is determined by error classification."""
    from tinker.agent.error_classifier import classify
    from tinker.code.github_tools import GitHubCodeProvider, compute_diff

    service = req.anomaly.get("service", "unknown")

    try:
        gh = GitHubCodeProvider(service=service)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    error_class = classify(req.anomaly)
    log.info("fix.start",
             actor=auth.subject,
             service=service,
             error_class=error_class.kind,
             confidence=error_class.confidence)

    if error_class.kind == "transient":
        staged = await _fix_transient(req.anomaly, error_class, gh)
    else:
        # logic_bug or unknown → deep investigation
        staged = await _fix_logic_bug(req.anomaly, error_class, gh)

    if not staged:
        raise HTTPException(status_code=422, detail="Agent did not produce a fix.")

    path = staged["path"]
    old_content = gh.get_file(path)
    diff = compute_diff(path, old_content, staged["new_content"])

    log.info("fix.done", actor=auth.subject, service=service, path=path,
             error_class=error_class.kind)
    return {
        "file_changes": [{"path": path, "new_content": staged["new_content"]}],
        "explanation": staged["explanation"],
        "diff": diff,
        "error_class": error_class.kind,
    }


@router.post("/approve")
async def approve(
    req: ApproveRequest,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> dict[str, Any]:
    """Apply staged file changes and open a GitHub PR. Requires oncall or sre-lead role."""
    if "oncall" not in auth.roles and "sre-lead" not in auth.roles:
        raise HTTPException(status_code=403, detail="Requires oncall or sre-lead role")

    try:
        from tinker.code.github_tools import GitHubCodeProvider
        gh = GitHubCodeProvider(service=req.service)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    branch = f"tinker/fix-{uuid.uuid4().hex[:8]}"
    title = f"fix: tinker auto-fix for {req.service}"

    try:
        gh.create_branch(branch)
        for change in req.file_changes:
            gh.update_file(
                path=change.path,
                new_content=change.new_content,
                commit_message=f"fix({req.service}): {change.path}",
                branch=branch,
            )
        pr_url = gh.create_pr(branch=branch, title=title, body=req.explanation)
    except Exception as exc:
        log.exception("approve.failed", service=req.service)
        raise HTTPException(status_code=500, detail=str(exc))

    log.info("approve.done", pr_url=pr_url, approved_by=auth.subject)
    return {"pr_url": pr_url}


# ── Fix agent implementations ─────────────────────────────────────────────────

async def _fix_transient(
    anomaly: dict[str, Any],
    error_class: "ErrorClass",
    gh: "GitHubCodeProvider",
) -> dict | None:
    """Minimal fix for transient/infrastructure errors.

    Only reads the specific files from the stack trace.
    No code search, no commit history.
    Max 4 turns — the fix should be a targeted configuration or guard change.
    """
    from tinker.agent import llm as llm_mod
    from tinker.config import settings
    from tinker.monitor.summarizer import build_explain_context

    context_block = build_explain_context(anomaly)

    # Pre-fetch stack trace files to include in the prompt (no agent turns wasted)
    call_site_code = _fetch_code_context(error_class, anomaly.get("service", ""), deep=False)

    system_prompt = (
        "You are an expert SRE fixing a TRANSIENT / INFRASTRUCTURE error.\n\n"
        f"Error classification: {error_class.reason}\n\n"
        "This is NOT a logic bug — do NOT refactor or redesign. "
        "Make the SMALLEST safe change at the specific call site:\n"
        "  - Add or fix a timeout parameter\n"
        "  - Add retry logic with backoff\n"
        "  - Add a circuit breaker or fallback\n"
        "  - Add input validation / null guard at the entry point\n"
        "  - Fix a connection pool size or configuration value\n\n"
        "Rules:\n"
        "  - Use github_get_file ONLY for files directly in the stack trace\n"
        "  - Do NOT call github_search_code or github_get_commits\n"
        "  - propose_fix takes the COMPLETE new file content\n"
        "  - Call propose_fix as soon as you identify the fix (max 4 turns)\n\n"
        "--- Anomaly Summary ---\n"
        f"{context_block}"
        + (f"\n\n--- Call Site Code ---\n{call_site_code}\n--- End Code ---"
           if call_site_code else "")
        + "\n--- End Summary ---"
    )

    # Transient fixes: only allow reading files and proposing fix
    tools = [
        _fn("github_get_file",
            "Read a file from the repository. Use only for files in the stack trace.",
            {"type": "object", "properties": {
                "path": {"type": "string"},
                "ref":  {"type": "string"},
            }, "required": ["path"]}),
        _fn("propose_fix",
            "Stage the fix. Provide COMPLETE new file content. Call once when ready.",
            {"type": "object", "properties": {
                "path":        {"type": "string"},
                "new_content": {"type": "string"},
                "explanation": {"type": "string"},
            }, "required": ["path", "new_content", "explanation"]}),
    ]

    return _run_agent_loop(system_prompt, tools, gh, max_turns=4)


async def _fix_logic_bug(
    anomaly: dict[str, Any],
    error_class: "ErrorClass",
    gh: "GitHubCodeProvider",
) -> dict | None:
    """Deep investigation for logic bugs.

    Reads stack trace files, searches for related patterns, checks commit history.
    Max 12 turns.
    """
    from tinker.agent import llm as llm_mod
    from tinker.config import settings
    from tinker.monitor.summarizer import build_explain_context

    context_block = build_explain_context(anomaly)
    call_site_code = _fetch_code_context(error_class, anomaly.get("service", ""), deep=True)

    system_prompt = (
        "You are an expert SRE and software engineer fixing a CODE LOGIC BUG.\n\n"
        f"Error classification: {error_class.reason}\n\n"
        "Strategy:\n"
        "1. Start with the stack trace files (already provided below if available)\n"
        "2. Use github_get_file to read additional context around the failure\n"
        "3. Use github_search_code to find related patterns — similar error handling, "
        "the same field/variable used elsewhere, related tests\n"
        "4. Use github_get_commits on the failing file to check for recent changes "
        "that may have introduced the bug\n"
        "5. Identify the root cause — be specific about the code path\n"
        "6. Call propose_fix with the COMPLETE updated file content\n\n"
        "Rules:\n"
        "  - propose_fix takes COMPLETE file content, not a diff\n"
        "  - Make the minimal correct fix — do not refactor unrelated code\n"
        "  - Call propose_fix once when confident (max 12 turns)\n\n"
        "--- Anomaly Summary ---\n"
        f"{context_block}"
        + (f"\n\n--- Stack Trace Files ---\n{call_site_code}\n--- End Code ---"
           if call_site_code else "")
        + "\n--- End Summary ---"
    )

    tools = [
        _fn("github_get_file",
            "Read a file from the GitHub repository.",
            {"type": "object", "properties": {
                "path": {"type": "string", "description": "File path relative to repo root"},
                "ref":  {"type": "string", "description": "Branch or commit SHA (optional)"},
            }, "required": ["path"]}),
        _fn("github_search_code",
            "Search the repository's code. Use to find related patterns, "
            "error handling, usages of the failing variable/function.",
            {"type": "object", "properties": {
                "query":       {"type": "string"},
                "max_results": {"type": "integer", "default": 10},
            }, "required": ["query"]}),
        _fn("github_get_commits",
            "List recent commits touching a file. Use to find what changed recently.",
            {"type": "object", "properties": {
                "path": {"type": "string", "default": "."},
                "n":    {"type": "integer", "default": 10},
            }}),
        _fn("propose_fix",
            "Stage the fix. Provide COMPLETE new file content (not a diff). "
            "Call once when confident.",
            {"type": "object", "properties": {
                "path":        {"type": "string"},
                "new_content": {"type": "string"},
                "explanation": {"type": "string"},
            }, "required": ["path", "new_content", "explanation"]}),
    ]

    return _run_agent_loop(system_prompt, tools, gh, max_turns=12)


def _run_agent_loop(
    system_prompt: str,
    tools: list[dict],
    gh: "GitHubCodeProvider",
    max_turns: int,
) -> dict | None:
    from tinker.agent import llm as llm_mod
    from tinker.config import settings

    messages: list[dict] = [{"role": "user", "content": system_prompt}]
    staged: dict | None = None

    for turn in range(max_turns):
        response = llm_mod.complete(
            messages, model=settings.default_model, tools=tools, max_tokens=8192
        )

        if llm_mod.is_tool_call(response):
            messages.append(llm_mod.assistant_message_from_response(response))
            for tc in llm_mod.extract_tool_calls(response):
                name, args = tc["name"], tc["arguments"]
                log.debug("fix.tool_call", turn=turn, tool=name, path=args.get("path", ""))

                if name == "propose_fix":
                    staged = args
                    result_str = "Fix staged. Task complete."
                else:
                    result_str = _dispatch_read_tool(name, args, gh)

                messages.append(llm_mod.tool_result_message(tc["id"], result_str))

            if staged:
                break
        else:
            break

    return staged


# ── Code context fetcher ──────────────────────────────────────────────────────

def _fetch_code_context(
    error_class: "ErrorClass",
    service: str,
    deep: bool,
) -> str:
    """Fetch code around stack trace frames from GitHub.

    For transient errors: only the top frame (the call site).
    For logic bugs: all extracted frames (up to 3 files).
    Returns empty string if GitHub is not configured or no files found.
    """
    if not error_class.stack_files:
        return ""

    try:
        from tinker.code.github_tools import GitHubCodeProvider
        gh = GitHubCodeProvider(service=service)
    except RuntimeError:
        return ""  # GitHub not configured — explain still works, just without code

    files_to_fetch = error_class.stack_files[:1] if not deep else error_class.stack_files[:3]
    sections: list[str] = []

    for path, lineno in files_to_fetch:
        content = gh.get_file(path)
        if content.startswith("(error"):
            continue
        # Extract window around the failing line
        lines = content.splitlines()
        start = max(0, lineno - _CODE_CONTEXT_LINES - 1)
        end = min(len(lines), lineno + _CODE_CONTEXT_LINES)
        window = lines[start:end]
        # Add line numbers for clarity
        numbered = "\n".join(
            f"{start + i + 1:4d}  {'→ ' if (start + i + 1) == lineno else '  '}{line}"
            for i, line in enumerate(window)
        )
        sections.append(f"# {path} (around line {lineno})\n```\n{numbered}\n```")

    return "\n\n".join(sections)


# ── Tool helpers ──────────────────────────────────────────────────────────────

def _fn(name: str, desc: str, params: dict) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": params}}


def _dispatch_read_tool(name: str, args: dict, gh: "GitHubCodeProvider") -> str:
    match name:
        case "github_get_file":
            return gh.get_file(args["path"], ref=args.get("ref"))
        case "github_search_code":
            return gh.search_code(args["query"], max_results=args.get("max_results", 10))
        case "github_get_commits":
            return gh.get_commits(path=args.get("path", "."), n=args.get("n", 10))
        case _:
            return f"Unknown tool: {name}"


# ── Type hint forward refs ────────────────────────────────────────────────────
from tinker.agent.error_classifier import ErrorClass  # noqa: E402
from tinker.code.github_tools import GitHubCodeProvider  # noqa: E402

"""Agent routes — LLM-powered explain, fix, approve, and rca.

POST /api/v1/explain   — stream LLM explanation, enriched with code context when available
POST /api/v1/fix       — run fix agent; depth driven by error classification
POST /api/v1/approve   — apply staged file changes and open a GitHub PR
POST /api/v1/rca       — stream full root-cause analysis combining logs + metrics + traces

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
    new_content: str  # full file content after applying the edit
    old_string: str = ""  # the exact original lines that were replaced
    new_string: str = ""  # the replacement lines


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
    from tinker import toml_config as tc
    from tinker.agent.summarizer import build_explain_context

    anomaly = req.anomaly
    service = anomaly.get("service", "unknown")

    # Classify to decide how much code context to fetch
    error_class = classify(anomaly)
    log.info(
        "explain.start",
        actor=auth.subject,
        metric=anomaly.get("metric"),
        error_class=error_class.kind,
        confidence=error_class.confidence,
    )

    context_block = build_explain_context(anomaly)
    code_context = _fetch_code_context(error_class, service, deep=error_class.kind == "logic_bug")

    # Build prompt — inject code context if available
    code_section = ""
    if code_context:
        code_section = (
            "\n\n--- Relevant Code (from GitHub) ---\n" + code_context + "\n--- End Code ---"
        )

    classification_hint = (
        f"\nError classification: {error_class.kind.upper()} ({error_class.reason})\n"
    )

    prompt = (
        "You are an expert SRE analysing a production anomaly.\n"
        + classification_hint
        + "Based on the anomaly summary"
        + (" and the relevant source code" if code_context else "")
        + " below, explain:\n"
        "1. What is happening (root cause hypothesis)\n"
        "2. Why it is happening (likely trigger)\n"
        "3. Immediate impact\n"
        "4. What to look at next\n\n"
        + (
            "For CONFIG ERRORS: identify the exact missing or misconfigured value from the log "
            "content (env var name, config key, API key name). State what needs to be set and where.\n"
            if error_class.kind == "config_error"
            else "For TRANSIENT errors: focus on configuration, retries, timeouts, circuit breakers.\n"
            if error_class.kind == "transient"
            else "For LOGIC BUGS: point to the exact file and line number. Explain the code path that leads to the failure.\n"
        )
        + "\nIMPORTANT: Read the log entries carefully. If the log content contains a specific "
        "error message, missing config key, HTTP response body, or dependency name — quote it "
        "directly and make it the centre of your explanation. Do NOT give a generic answer when "
        "the logs contain the specific root cause.\n"
        "\nBe concise. Focus on actionable insight.\n\n"
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
                model=tc.get().llm.default_model,
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
    log.info(
        "fix.start",
        actor=auth.subject,
        service=service,
        error_class=error_class.kind,
        confidence=error_class.confidence,
    )

    if error_class.kind == "transient":
        staged = await _fix_transient(req.anomaly, error_class, gh)
    else:
        # logic_bug or unknown → deep investigation
        staged = await _fix_logic_bug(req.anomaly, error_class, gh)

    if not staged:
        raise HTTPException(
            status_code=422,
            detail=(
                "Agent did not produce a fix. "
                "The model may have responded with text instead of calling propose_fix. "
                "Check server logs for 'fix.no_tool_call' or 'fix.llm_error'."
            ),
        )

    path = staged["path"]
    old_content = gh.get_file(path)
    diff = compute_diff(path, old_content, staged["new_content"])

    log.info(
        "fix.done", actor=auth.subject, service=service, path=path, error_class=error_class.kind
    )
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


# ── Language detection ────────────────────────────────────────────────────────

_LANG_RULES: dict[str, str] = {
    "go": (
        "LANGUAGE: Go\n"
        "  - Use tabs for indentation (gofmt standard) — do NOT change existing indentation\n"
        "  - Error handling: return (value, error) — use `if err != nil` checks\n"
        "  - Retry logic: use a simple loop or golang.org/x/net/context; do NOT add new dependencies\n"
        "  - Timeouts: use context.WithTimeout — pass context through the call chain\n"
        "  - Do NOT add fmt.Println or log.Println for debugging\n"
        "  - Preserve all existing import grouping (stdlib / external / internal)\n"
        "  - Only add imports that are actually used"
    ),
    "java": (
        "LANGUAGE: Java\n"
        "  - Use 4-space indentation — do NOT change existing indentation\n"
        "  - Retry logic: use a simple loop with Thread.sleep(); avoid new frameworks\n"
        "  - Timeouts: set on the existing client/connection object — do not redesign\n"
        "  - Preserve existing exception hierarchy — catch specific exceptions, not Exception\n"
        "  - Do NOT add System.out.println or logging changes unrelated to the fix\n"
        "  - Keep existing annotation and import order"
    ),
    "python": (
        "LANGUAGE: Python\n"
        "  - Use 4-space indentation — do NOT change existing indentation\n"
        "  - Retry logic: use tenacity or a manual loop — do not add new dependencies unless already present\n"
        "  - Timeouts: pass timeout= to existing client calls\n"
        "  - Preserve existing type annotations exactly as found\n"
        "  - Do NOT change string quote style or add/remove blank lines unrelated to the fix\n"
        "  - Keep existing import order (stdlib / third-party / local)"
    ),
    "typescript": (
        "LANGUAGE: TypeScript / JavaScript\n"
        "  - Preserve existing indentation (spaces or tabs) exactly\n"
        "  - Retry logic: use a simple async loop with setTimeout-based delay\n"
        "  - Timeouts: use AbortController or the existing client's timeout option\n"
        "  - Preserve existing async/await vs Promise chain style\n"
        "  - Do NOT change semicolon or quote style"
    ),
    "ruby": (
        "LANGUAGE: Ruby\n"
        "  - Use 2-space indentation — do NOT change existing indentation\n"
        "  - Retry logic: use the built-in `retry` keyword inside rescue\n"
        "  - Preserve existing method visibility and module structure"
    ),
}

_EXT_TO_LANG: dict[str, str] = {
    ".go": "go",
    ".java": "java",
    ".py": "python",
    ".ts": "typescript",
    ".js": "typescript",
    ".rb": "ruby",
}


def _detect_language(error_class: "ErrorClass", code_context: str) -> str | None:
    """Detect the primary language from stack trace file extensions."""
    import os

    for path, _ in error_class.stack_files:
        ext = os.path.splitext(path)[1].lower()
        if ext in _EXT_TO_LANG:
            return _LANG_RULES[_EXT_TO_LANG[ext]]
    # Fallback: scan code context header lines for file extensions
    for line in code_context.splitlines()[:10]:
        for ext, lang in _EXT_TO_LANG.items():
            if ext in line:
                return _LANG_RULES[lang]
    return None


_UNIVERSAL_FIX_RULES = (
    "CRITICAL RULES — apply to every language:\n"
    "  - Change ONLY the lines needed to fix the specific bug — nothing else\n"
    "  - Do NOT reformat, re-indent, or reorganise code that is not part of the fix\n"
    "  - Do NOT rename variables, methods, or classes\n"
    "  - Do NOT add comments or docstrings unless they explain the fix directly\n"
    "  - Do NOT remove existing comments or blank lines\n"
    "  - The diff between old and new file must contain ONLY the bug fix"
)


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
    from tinker.agent.summarizer import build_explain_context

    context_block = build_explain_context(anomaly)

    # Pre-fetch stack trace files to include in the prompt (no agent turns wasted)
    call_site_code = _fetch_code_context(error_class, anomaly.get("service", ""), deep=False)
    lang_rules = _detect_language(error_class, call_site_code)

    system_prompt = (
        "You are an expert SRE fixing a TRANSIENT / INFRASTRUCTURE error.\n\n"
        f"Error classification: {error_class.reason}\n\n"
        + (f"{lang_rules}\n\n" if lang_rules else "")
        + f"{_UNIVERSAL_FIX_RULES}\n\n"
        "This is NOT a logic bug — do NOT refactor or redesign. "
        "Make the SMALLEST safe change at the specific call site:\n"
        "  - Add or fix a timeout parameter\n"
        "  - Add retry logic with backoff\n"
        "  - Add a circuit breaker or fallback\n"
        "  - Add input validation / null guard at the entry point\n"
        "  - Fix a connection pool size or configuration value\n\n"
        "Tool rules:\n"
        "  - Use github_get_file ONLY for files directly in the stack trace\n"
        "  - Do NOT call github_search_code or github_get_commits\n"
        "  - propose_edit takes only the lines that change (old_string → new_string)\n"
        "  - Call propose_edit as soon as you identify the fix (max 4 turns)\n\n"
        "--- Anomaly Summary ---\n"
        f"{context_block}"
        + (
            f"\n\n--- Call Site Code ---\n{call_site_code}\n--- End Code ---"
            if call_site_code
            else ""
        )
        + "\n--- End Summary ---"
    )

    # Transient fixes: only allow reading files and proposing fix
    tools = [
        _fn(
            "github_get_file",
            "Read a file from the repository. Use only for files in the stack trace.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "ref": {"type": "string"},
                },
                "required": ["path"],
            },
        ),
        _fn(
            "propose_edit",
            "Stage a surgical edit: provide ONLY the lines to change, not the whole file. "
            "old_string must be copied EXACTLY from the file (correct indentation/whitespace). "
            "If old_string is not found verbatim the edit is rejected and you must retry.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Repo-relative file path"},
                    "old_string": {
                        "type": "string",
                        "description": "The EXACT lines to replace, copied verbatim from github_get_file. "
                        "Must be unique in the file. Include enough surrounding context "
                        "(function signature, closing brace) to make it unambiguous.",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "The replacement lines. Must preserve the file's indentation style.",
                    },
                    "explanation": {
                        "type": "string",
                        "description": "What was wrong and exactly what lines were changed and why",
                    },
                },
                "required": ["path", "old_string", "new_string", "explanation"],
            },
        ),
    ]

    from tinker import toml_config as tc

    return await _run_agent_loop(
        system_prompt, tools, gh, max_turns=4, model=tc.get().llm.deep_rca_model
    )


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
    from tinker.agent.summarizer import build_explain_context

    context_block = build_explain_context(anomaly)
    call_site_code = _fetch_code_context(error_class, anomaly.get("service", ""), deep=True)
    lang_rules = _detect_language(error_class, call_site_code)

    system_prompt = (
        "You are an expert SRE and software engineer fixing a CODE LOGIC BUG.\n\n"
        f"Error classification: {error_class.reason}\n\n"
        + (f"{lang_rules}\n\n" if lang_rules else "")
        + f"{_UNIVERSAL_FIX_RULES}\n\n"
        "Investigation strategy:\n"
        "1. Start with the stack trace files (already provided below if available)\n"
        "2. Use github_get_file to read additional context around the failure\n"
        "3. Use github_search_code to find related patterns — similar error handling, "
        "the same field/variable used elsewhere, related tests\n"
        "4. Use github_get_commits on the failing file to check for recent changes "
        "that may have introduced the bug\n"
        "5. Identify the root cause — be specific about the code path and line\n"
        "6. Call propose_edit with the EXACT lines that need to change (old_string → new_string)\n\n"
        "Tool rules:\n"
        "  - propose_edit takes only the lines that change (old_string → new_string)\n"
        "  - old_string must be copied EXACTLY from the file — correct indentation\n"
        "  - Make the minimal correct fix — do not touch unrelated code\n"
        "  - Call propose_edit once when confident (max 12 turns)\n\n"
        "--- Anomaly Summary ---\n"
        f"{context_block}"
        + (
            f"\n\n--- Stack Trace Files ---\n{call_site_code}\n--- End Code ---"
            if call_site_code
            else ""
        )
        + "\n--- End Summary ---"
    )

    tools = [
        _fn(
            "github_get_file",
            "Read a file from the GitHub repository.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to repo root"},
                    "ref": {"type": "string", "description": "Branch or commit SHA (optional)"},
                },
                "required": ["path"],
            },
        ),
        _fn(
            "github_search_code",
            "Search the repository's code. Use to find related patterns, "
            "error handling, usages of the failing variable/function.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            },
        ),
        _fn(
            "github_get_commits",
            "List recent commits touching a file. Use to find what changed recently.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "."},
                    "n": {"type": "integer", "default": 10},
                },
            },
        ),
        _fn(
            "propose_edit",
            "Stage a surgical edit: provide ONLY the lines to change, not the whole file. "
            "old_string must be copied EXACTLY from the file (correct indentation/whitespace). "
            "If old_string is not found verbatim the edit is rejected and you must retry.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Repo-relative file path"},
                    "old_string": {
                        "type": "string",
                        "description": "The EXACT lines to replace, copied verbatim from github_get_file. "
                        "Must be unique in the file. Include enough surrounding context "
                        "(function signature, closing brace) to make it unambiguous.",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "The replacement lines. Must preserve the file's indentation style.",
                    },
                    "explanation": {
                        "type": "string",
                        "description": "What was wrong and exactly what lines were changed and why",
                    },
                },
                "required": ["path", "old_string", "new_string", "explanation"],
            },
        ),
    ]

    from tinker import toml_config as tc

    return await _run_agent_loop(
        system_prompt, tools, gh, max_turns=12, model=tc.get().llm.deep_rca_model
    )


async def _run_agent_loop(
    system_prompt: str,
    tools: list[dict],
    gh: "GitHubCodeProvider",
    max_turns: int,
    model: str | None = None,
) -> dict | None:
    from tinker.agent import llm as llm_mod
    from tinker import toml_config as tc

    model = model or tc.get().llm.default_model
    messages: list[dict] = [{"role": "user", "content": system_prompt}]
    staged: dict | None = None

    for turn in range(max_turns):
        try:
            response = await llm_mod.async_complete(
                messages, model=model, tools=tools, max_tokens=8192
            )
        except Exception as exc:
            log.error("fix.llm_error", turn=turn, model=model, error=str(exc))
            raise HTTPException(status_code=502, detail=f"LLM error on turn {turn}: {exc}")

        if llm_mod.is_tool_call(response):
            messages.append(llm_mod.assistant_message_from_response(response))
            for tool_call in llm_mod.extract_tool_calls(response):
                name, args = tool_call["name"], tool_call["arguments"]
                log.debug("fix.tool_call", turn=turn, tool=name, path=args.get("path", ""))

                if name == "propose_edit":
                    resolved_path, new_content, error = _apply_edit(
                        args.get("path", ""),
                        args.get("old_string", ""),
                        args.get("new_string", ""),
                        gh,
                    )
                    if error:
                        log.warning("fix.edit_rejected", turn=turn, reason=error[:120])
                        result_str = error
                    else:
                        staged = {
                            "path": resolved_path,
                            "new_content": new_content,
                            "old_string": args.get("old_string", ""),
                            "new_string": args.get("new_string", ""),
                            "explanation": args.get("explanation", ""),
                        }
                        result_str = "Edit staged. Task complete."
                else:
                    result_str = _dispatch_read_tool(name, args, gh)

                messages.append(llm_mod.tool_result_message(tool_call["id"], result_str))

            if staged:
                break
        else:
            # LLM returned text instead of a tool call — log it so it's visible
            text = llm_mod.extract_text(response)
            log.warning("fix.no_tool_call", turn=turn, text_preview=text[:200])
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


# ── Edit application ──────────────────────────────────────────────────────────


def _apply_edit(
    path: str, old_string: str, new_string: str, gh: "GitHubCodeProvider"
) -> tuple[str, str, str | None]:
    """Apply an old_string → new_string edit to a file fetched from GitHub.

    Returns (resolved_path, new_full_content, error_message).
    error_message is None on success.
    resolved_path may differ from path when get_file resolves a container-relative path.

    Mirrors how Claude Code's Edit tool works:
      - Fetch the current file content
      - Verify old_string exists verbatim (catches hallucinated edits)
      - Replace first occurrence with new_string
      - Return the resolved repo path + complete updated file content
    """
    resolved_path = path
    original = gh.get_file(path)
    if original.startswith("("):
        return path, "", f"REJECTED: could not read file '{path}': {original}"

    # Strip the "# resolved 'x' → 'y'" header that get_file prepends for path-resolved files,
    # and capture the actual repo path so approve uses the correct path.
    if original.startswith("# resolved "):
        header, _, original = original.partition("\n")
        # header format: "# resolved '/app/main.go' → 'services/inventory/main.go'"
        if " → '" in header:
            resolved_path = header.split(" → '", 1)[1].rstrip("'")

    if old_string not in original:
        # Try normalising line endings in case of CRLF mismatch
        normalised = original.replace("\r\n", "\n")
        old_normalised = old_string.replace("\r\n", "\n")
        if old_normalised in normalised:
            return resolved_path, normalised.replace(old_normalised, new_string, 1), None

        # Count how many lines matched to give the model useful feedback
        old_lines = old_string.splitlines()
        matched = sum(1 for l in old_lines if l in original)
        return (
            resolved_path,
            "",
            (
                f"REJECTED: old_string not found verbatim in '{path}' "
                f"({matched}/{len(old_lines)} lines matched). "
                "Read the file again with github_get_file and copy the EXACT lines "
                "you want to replace — including correct indentation and whitespace."
            ),
        )

    new_content = original.replace(old_string, new_string, 1)
    return resolved_path, new_content, None


# ── RCA ───────────────────────────────────────────────────────────────────────


class RcaRequest(BaseModel):
    service: str
    since: str = "1h"
    severity_filter: str | None = None  # e.g. "high" — only include anomalies at this level+


@router.post("/rca")
async def rca(
    req: RcaRequest,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> StreamingResponse:
    """Stream a full root-cause analysis combining logs, metrics, traces, and code context."""
    from tinker.agent import llm as llm_mod
    from tinker.backends import get_backend_for_service
    from tinker.backends.base import ServiceNotFoundError
    from tinker.agent.summarizer import build_explain_context

    async def _stream() -> AsyncIterator[str]:
        try:
            backend = get_backend_for_service(req.service)
        except Exception as exc:
            yield _sse(f"[ERROR] Could not load backend: {exc}")
            yield "data: [DONE]\n\n"
            return

        # ── Gather evidence in parallel ──────────────────────────────────────
        from datetime import timedelta, timezone

        unit = req.since[-1]
        value = int(req.since[:-1])
        delta = {
            "m": timedelta(minutes=value),
            "h": timedelta(hours=value),
            "d": timedelta(days=value),
        }.get(unit, timedelta(hours=1))
        window_minutes = int(delta.total_seconds() / 60)

        import asyncio as _asyncio

        try:
            anomalies, traces = await _asyncio.gather(
                backend.detect_anomalies(req.service, window_minutes=min(window_minutes, 60)),
                backend.get_traces(req.service, since=req.since, limit=10),
                return_exceptions=True,
            )
        except Exception as exc:
            yield _sse(f"[ERROR] Evidence gathering failed: {exc}")
            yield "data: [DONE]\n\n"
            return

        if isinstance(anomalies, Exception):
            anomalies = []
        if isinstance(traces, Exception):
            traces = []

        # Apply severity filter
        if req.severity_filter and isinstance(anomalies, list):
            _order = ["low", "medium", "high", "critical"]
            min_idx = (
                _order.index(req.severity_filter.lower())
                if req.severity_filter.lower() in _order
                else 0
            )
            anomalies = [
                a
                for a in anomalies
                if _order.index(a.severity.lower() if a.severity.lower() in _order else "low")
                >= min_idx
            ]

        # ── Build RCA prompt ─────────────────────────────────────────────────
        anomaly_section = ""
        if anomalies:
            parts = []
            for a in anomalies[:5]:
                ctx = build_explain_context(a.to_dict())
                parts.append(f"### Anomaly: {a.metric} ({a.severity.upper()})\n{ctx}")
            anomaly_section = "\n\n".join(parts)
        else:
            anomaly_section = "No anomalies detected in the window."

        trace_section = ""
        if traces:
            lines = []
            for t in traces[:5]:
                td = t.to_dict()
                status_marker = "ERROR" if td["status"] == "error" else "ok"
                lines.append(
                    f"- trace_id={td['trace_id']} op={td['operation_name']} "
                    f"duration={td['duration_ms']:.0f}ms spans={td['span_count']} status={status_marker}"
                )
            trace_section = "Recent traces:\n" + "\n".join(lines)
        else:
            trace_section = "No trace data available."

        # Optionally pull code context from the highest-severity anomaly
        code_section = ""
        if anomalies:
            from tinker.agent.error_classifier import classify

            top = max(
                anomalies,
                key=lambda a: ["low", "medium", "high", "critical"].index(
                    a.severity.lower()
                    if a.severity.lower() in ["low", "medium", "high", "critical"]
                    else "low"
                ),
            )
            ec = classify(top.to_dict())
            code_context = _fetch_code_context(ec, req.service, deep=ec.kind == "logic_bug")
            if code_context:
                code_section = (
                    "\n\n--- Relevant Code (from GitHub) ---\n"
                    + code_context
                    + "\n--- End Code ---"
                )

        prompt = (
            f"You are a senior SRE performing root cause analysis for service **{req.service}**.\n"
            f"Analysis window: {req.since}\n\n"
            "## Your task\n"
            "Produce a structured RCA report with these sections:\n"
            "1. **Executive Summary** — one paragraph, what broke and why\n"
            "2. **Root Cause** — the specific technical cause, with evidence\n"
            "3. **Contributing Factors** — secondary causes or amplifiers\n"
            "4. **Timeline** — reconstruct what happened and when\n"
            "5. **Immediate Actions** — what to do right now (with commands if applicable)\n"
            "6. **Prevention** — how to prevent recurrence\n\n"
            "## Evidence\n\n"
            "### Anomalies\n"
            f"{anomaly_section}\n\n"
            "### Distributed Traces\n"
            f"{trace_section}"
            f"{code_section}\n\n"
            "Be specific, cite evidence, and prioritise actionability over completeness."
        )

        log.info(
            "rca.start",
            service=req.service,
            since=req.since,
            anomaly_count=len(anomalies),
            trace_count=len(traces),
            actor=auth.subject,
        )

        llm = llm_mod.get_llm()
        async for chunk in llm.stream([{"role": "user", "content": prompt}]):
            yield _sse(chunk)
        yield "data: [DONE]\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


# ── Tool helpers ──────────────────────────────────────────────────────────────


def _fn(name: str, desc: str, params: dict) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "description": desc, "parameters": params},
    }


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

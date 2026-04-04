"""Agent routes — LLM-powered explain, fix, and approve.

POST /api/v1/explain   — stream LLM explanation for an anomaly (SSE)
POST /api/v1/fix       — run fix agent with GitHub code tools, return file changes
POST /api/v1/approve   — apply staged file changes and open a GitHub PR

Fix agent uses GitHub API tools (no local clone required):
  github_get_file       — read a file from the repo
  github_search_code    — search repo code via GitHub search API
  github_get_commits    — recent commits touching a path
  propose_fix           — stage {path, new_content, explanation} (terminal tool)
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


# ── Request models ────────────────────────────────────────────────────────────

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
    """Stream an LLM explanation for an anomaly."""
    from tinker.agent import llm as llm_mod
    from tinker.monitor.summarizer import build_explain_context
    from tinker.config import settings

    context_block = build_explain_context(req.anomaly)
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

    log.info("explain.start", actor=auth.subject, metric=req.anomaly.get("metric"))

    async def _stream() -> AsyncIterator[str]:
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
    """Run the fix agent with GitHub code tools. Returns {file_changes, explanation, diff}."""
    from tinker.agent import llm as llm_mod
    from tinker.code.github_tools import GitHubCodeProvider, compute_diff
    from tinker.config import settings
    from tinker.monitor.summarizer import build_explain_context

    service = req.anomaly.get("service", "unknown")

    try:
        gh = GitHubCodeProvider(service=service)
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    context_block = build_explain_context(req.anomaly)

    system_prompt = (
        "You are an expert SRE and software engineer. "
        "You have access to the service's GitHub repository. "
        "Given the anomaly summary below, investigate the code and propose a fix.\n\n"
        "Strategy:\n"
        "1. Extract file paths and line numbers from the stack trace\n"
        "2. Use github_get_file to read those files\n"
        "3. Use github_search_code to find related patterns (error handling, similar code)\n"
        "4. Use github_get_commits on relevant files to understand recent changes\n"
        "5. Identify the root cause\n"
        "6. Call propose_fix with the full updated file content and a clear explanation\n\n"
        "Rules:\n"
        "- propose_fix takes the COMPLETE new file content, not a diff\n"
        "- Make the minimal change needed — do not refactor unrelated code\n"
        "- propose_fix is the terminal tool — call it once when confident\n\n"
        "--- Anomaly Summary ---\n"
        f"{context_block}\n"
        "--- End Summary ---"
    )

    tools = [
        _fn("github_get_file",
            "Read a file from the GitHub repository.",
            {"type": "object", "properties": {
                "path": {"type": "string", "description": "File path relative to repo root, e.g. src/payments/processor.py"},
                "ref":  {"type": "string", "description": "Branch or commit SHA (default: default branch)"},
            }, "required": ["path"]}),

        _fn("github_search_code",
            "Search the repository's code using GitHub code search.",
            {"type": "object", "properties": {
                "query":       {"type": "string", "description": "Search terms, e.g. 'StripeError timeout'"},
                "max_results": {"type": "integer", "default": 10},
            }, "required": ["query"]}),

        _fn("github_get_commits",
            "List recent commits touching a file or directory.",
            {"type": "object", "properties": {
                "path": {"type": "string", "description": "File or directory path (default: repo root)"},
                "n":    {"type": "integer", "default": 10},
            }}),

        _fn("propose_fix",
            "Stage a fix. Provide the COMPLETE updated file content (not a diff). "
            "This is the terminal tool — call it once when you know the fix.",
            {"type": "object", "properties": {
                "path":        {"type": "string", "description": "File path to update"},
                "new_content": {"type": "string", "description": "Complete new file content"},
                "explanation": {"type": "string", "description": "Clear explanation of what changed and why"},
            }, "required": ["path", "new_content", "explanation"]}),
    ]

    messages: list[dict] = [{"role": "user", "content": system_prompt}]
    staged: dict | None = None
    MAX_TURNS = 12

    log.info("fix.start", actor=auth.subject, service=service)

    for turn in range(MAX_TURNS):
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
                    result_str = "Fix staged. Call complete."
                else:
                    result_str = _dispatch_read_tool(name, args, gh)

                messages.append(llm_mod.tool_result_message(tc["id"], result_str))

            if staged:
                break
        else:
            break

    if not staged:
        raise HTTPException(status_code=422, detail="Agent did not produce a fix.")

    # Compute a unified diff for display in the REPL
    path = staged["path"]
    old_content = gh.get_file(path)
    diff = compute_diff(path, old_content, staged["new_content"])

    log.info("fix.done", actor=auth.subject, service=service, path=path)
    return {
        "file_changes": [{"path": path, "new_content": staged["new_content"]}],
        "explanation": staged["explanation"],
        "diff": diff,
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

"""Agent routes — LLM-powered explain, fix, and approve.

POST /api/v1/explain   — stream LLM explanation for an anomaly (SSE)
POST /api/v1/fix       — run fix agent loop, return diff + explanation
POST /api/v1/approve   — apply a staged diff and open a GitHub PR
"""

from __future__ import annotations

import json
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


class ApproveRequest(BaseModel):
    diff: str
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
    """Run the fix agent loop against the server-side repo. Returns {diff, explanation}."""
    from tinker.config import settings
    from tinker.monitor.summarizer import build_explain_context
    from tinker.agent import llm as llm_mod
    from tinker.code.repo import RepoClient

    repo_path = settings.tinker_repo_path
    if not repo_path:
        raise HTTPException(
            status_code=422,
            detail="TINKER_REPO_PATH is not configured on the server.",
        )

    service = req.anomaly.get("service", "unknown")
    context_block = build_explain_context(req.anomaly)

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

    log.info("fix.start", actor=auth.subject, service=service)

    tools = [
        _fn("glob_files", "Find files by glob pattern in the repo.",
            {"type": "object", "properties": {
                "pattern": {"type": "string"},
                "max_results": {"type": "integer", "default": 20},
            }, "required": ["pattern"]}),
        _fn("get_file", "Read a source file from the repo.",
            {"type": "object", "properties": {
                "path": {"type": "string"},
            }, "required": ["path"]}),
        _fn("search_code", "Search the codebase for a pattern.",
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
                "diff": {"type": "string"},
                "explanation": {"type": "string"},
            }, "required": ["diff", "explanation"]}),
    ]

    repo = RepoClient(repo_path)
    messages: list[dict] = [{"role": "user", "content": system_prompt}]
    result: dict | None = None
    MAX_TURNS = 10

    for turn in range(MAX_TURNS):
        response = llm_mod.complete(messages, model=settings.default_model, tools=tools, max_tokens=4096)
        if llm_mod.is_tool_call(response):
            messages.append(llm_mod.assistant_message_from_response(response))
            for tc in llm_mod.extract_tool_calls(response):
                tool_result = _dispatch_tool(tc["name"], tc["arguments"], repo)
                if tc["name"] == "suggest_fix":
                    result = tc["arguments"]
                messages.append(llm_mod.tool_result_message(tc["id"], tool_result))
            if result:
                break
        else:
            break

    if not result:
        raise HTTPException(status_code=422, detail="Agent did not produce a fix.")

    log.info("fix.done", actor=auth.subject, service=service, has_diff=bool(result.get("diff")))
    return result


@router.post("/approve")
async def approve(
    req: ApproveRequest,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> dict[str, Any]:
    """Apply a staged diff and open a GitHub PR. Requires oncall or sre-lead role."""
    if "oncall" not in auth.roles and "sre-lead" not in auth.roles:
        raise HTTPException(status_code=403, detail="Requires oncall or sre-lead role")

    from tinker.config import settings
    from tinker.code.fix_applier import FixApplier
    import uuid

    repo_path = settings.tinker_repo_path
    if not repo_path:
        raise HTTPException(status_code=422, detail="TINKER_REPO_PATH is not configured on the server.")

    branch = f"tinker/fix-{uuid.uuid4().hex[:8]}"
    applier = FixApplier(repo_path=repo_path)

    try:
        pr_url = await applier.create_pr(
            diff=req.diff,
            branch_name=branch,
            title=f"fix: tinker auto-fix for {req.service}",
            body=req.explanation,
        )
    except Exception as exc:
        log.exception("approve.failed", service=req.service)
        raise HTTPException(status_code=500, detail=str(exc))

    log.info("approve.done", pr_url=pr_url, approved_by=auth.subject)
    return {"pr_url": pr_url}


# ── Tool helpers ──────────────────────────────────────────────────────────────

def _fn(name: str, desc: str, params: dict) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": params}}


def _dispatch_tool(name: str, args: dict, repo) -> str:
    import glob as glob_mod
    import os

    match name:
        case "glob_files":
            pattern = args.get("pattern", "**/*")
            max_r = args.get("max_results", 20)
            matches = glob_mod.glob(os.path.join(str(repo._root), pattern), recursive=True)
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
            commits = repo.recent_commits(service_path=args.get("path", "."), n=args.get("n", 10))
            return "\n".join(
                f"{c['sha'][:8]} {c['date'][:10]} {c['author']} — {c['subject']}"
                for c in commits
            ) or "(no commits)"
        case "suggest_fix":
            return "Fix staged. Awaiting approval."
        case _:
            return f"Unknown tool: {name}"


def _is_binary(path: str) -> bool:
    import os
    _BINARY_EXTS = {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
                   ".pdf", ".zip", ".gz", ".tar", ".whl", ".so", ".dylib"}
    return os.path.splitext(path)[1].lower() in _BINARY_EXTS

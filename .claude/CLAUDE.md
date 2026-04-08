# CLAUDE.md — Tinkr

Coding rules and context for the Tinkr codebase.

---

## Codebase layout

```
src/tinker/
├── backends/       ObservabilityBackend ABC + one file per provider
├── mcp_servers/    MCP wrappers — stdio (local dev) or via /mcp/sse (remote)
├── server/         FastAPI app, auth, SSE routes, MCP-over-SSE endpoint
├── agent/          Claude orchestrator, tool definitions, guardrails
├── interfaces/     CLI (Typer) + Slack bot (Bolt)
├── monitor/        Background anomaly detection loop
├── code/           Git/GitHub integration, fix application
└── config.py       All env vars (pydantic-settings)
```

Config and secrets live in `~/.tinkr/` (written by the setup wizards).

---

## Backends

All backends implement `ObservabilityBackend` (ABC in `src/tinker/backends/base.py`).
Selected by `TINKR_BACKEND` env var at startup. The agent never imports a specific backend class directly.

| Backend | File | Auth |
|---|---|---|
| `cloudwatch` | `backends/cloudwatch.py` | IAM role |
| `gcp` | `backends/gcp.py` | Workload Identity |
| `azure` | `backends/azure.py` | Managed Identity |
| `grafana` | `backends/grafana.py` | API key |
| `datadog` | `backends/datadog.py` | API key + App key |
| `elastic` | `backends/elastic.py` | API key |
| `otel` | `backends/otel.py` | API key |

### Adding a new backend

1. `src/tinker/backends/<name>.py` — subclass `ObservabilityBackend`, implement `query_logs`, `get_metrics`, `detect_anomalies`
2. Register in `src/tinker/backends/__init__.py` `_REGISTRY`
3. Add env vars to `src/tinker/config.py` and `.env.example`
4. `src/tinker/mcp_servers/<name>_server.py` — subclass `TinkerMCPServer`
5. Add script entry point in `pyproject.toml`
6. Tests in `tests/test_backends/test_<name>.py`

---

## MCP tool naming

- Local stdio servers: `<backend>_<verb>_<noun>` — e.g. `cloudwatch_query_logs`
- Remote unified server (`/mcp/sse`): generic names — `query_logs`, `get_metrics`, `detect_anomalies`
- Never use single-word names — they collide across servers
- MCP servers must be stateless — no session state or in-memory caches between calls

---

## Agent module — development practices

The `agent/` module is the AI core: it drives the RCA loop, calls observability tools, reads source code, and proposes fixes. Key files:

| File | Purpose |
|---|---|
| `orchestrator.py` | Main agentic loop — model routing, tool dispatch, session state |
| `tools.py` | Tool schema definitions (OpenAI function-call format) + `ToolDispatcher` |
| `guardrails.py` | `ApprovalRequired` gate, RBAC role→tool mapping, audit log |
| `prompts.py` | System prompts for RCA and fix personas |
| `llm.py` | LiteLLM wrapper — model selection, retries, streaming |
| `error_classifier.py` | Classify errors as `transient / logic_bug / config_error / dependency_down` |
| `summarizer.py` | Compress long log/metric context before sending to LLM |

### Tool design rules

- Tool schemas use the **OpenAI function-call format** (`{"type": "function", "function": {...}}`). LiteLLM translates these for each provider automatically.
- Every tool that reads data must pass its output through `sanitize_log_content()` before returning — never return raw log strings to the LLM.
- Write tools (`apply_fix`, `create_pr`, `restart_service`, `rollback_deploy`) must be listed in `APPROVAL_REQUIRED_TOOLS` in `guardrails.py`. The dispatcher checks this before execution.
- Keep tool descriptions precise and action-oriented — the LLM decides which tool to call based solely on the description. Vague descriptions cause wrong tool selection.
- Tools must be stateless — no instance variables, no shared mutable state between calls.

### Agentic loop rules

- The orchestrator runs a tool-call loop: LLM response → dispatch tool → append result → repeat until no more tool calls.
- **Always query logs and metrics first, then correlate with code.** Enforce this in the system prompt and in the orchestrator's tool ordering hints.
- Use `claude-sonnet-4-6` for triage/monitoring; escalate to `claude-opus-4-6` with extended thinking only for confirmed high-severity incidents. Model selection lives in `llm.py` — never hardcode elsewhere.
- Long context (many log lines, large files) must go through `summarizer.py` before being appended to the message history. Never let the context window fill with raw log dumps.
- The agent must produce structured output (`IncidentReport` dataclass) — not free-form text. Parse and validate before returning from the orchestrator.

### Guardrails rules

- `APPROVAL_REQUIRED_TOOLS` in `guardrails.py` is the authoritative list of write tools. Keep it in sync with any new tools added in `tools.py`.
- `ROLE_PERMISSIONS` maps Slack/API roles (`dev`, `sre`, `oncall`, `sre-lead`) to allowed tools. New tools must be explicitly added to the appropriate roles — default is no access.
- Never catch and swallow `PendingApprovalError` — it must propagate to the interface layer (CLI/Slack/API) so the user sees the approval prompt.
- Use `MockApproval` in tests — never call `apply_fix` or `create_pr` against real systems in the test suite.

### Prompt rules

- System prompts live in `prompts.py` — never inline them in `orchestrator.py` or route handlers.
- The RCA prompt persona is "Tinkr, an expert SRE and software debugger". Keep it consistent.
- Prompts must explicitly instruct the model to: cite log lines and line numbers, use the severity scale (`critical/high/medium/low`), produce minimal diffs, and never include credentials or PII in output.

---

## Non-negotiable design rules

**Human-in-the-loop** — `apply_fix` and `create_pr` always require explicit approval. Never bypass the `ApprovalRequired` guardrail. Use `MockApproval` in tests.

**Secrets never reach the LLM** — always call `sanitize_log_content()` in `guardrails.py` before including log data in a prompt or returning it from an MCP tool.

**Model routing** — use `claude-sonnet-4-6` for monitoring/triage, `claude-opus-4-6` with `thinking` for deep RCA. Set this in `Orchestrator` only — never hardcode model names elsewhere.

**Backend is selected at startup** — `TINKR_BACKEND` is read once. Don't add per-request backend switching.

---

## Testing rules

- AWS backends: use `moto` mocks — never hit real AWS
- GCP / Azure / Datadog: `pytest-mock` + `respx` for httpx mocks
- LLM: mock `anthropic.Anthropic` — no real API calls in tests
- Server routes: FastAPI `TestClient`

---

## What NOT to do

- Don't hardcode cloud region, account IDs, or project IDs
- Don't store cloud credentials in env vars on the server — use native identity (IAM role / Workload Identity / Managed Identity)
- Don't bypass `ApprovalRequired` outside of tests
- Don't send raw log data to the LLM without `sanitize_log_content()` first
- Don't put session state in MCP servers
- Don't put `github_create_pr` or `apply_fix` in the permissions `allow` list
- Don't commit `.env` files or files containing real credentials
- Don't use `AZURE_CLIENT_SECRET` in production — use Managed Identity
- Don't add `--no-verify` to git commands

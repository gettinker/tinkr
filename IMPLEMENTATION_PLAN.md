# Tinker — Implementation Plan

## Status legend
- ✅ Complete
- 🔄 Partial / in progress
- [ ] Not started

---

## Phase 0: Project Skeleton ✅

- [x] Directory structure and `pyproject.toml`
- [x] `ObservabilityBackend` ABC and data models (`LogEntry`, `MetricPoint`, `Anomaly`)
- [x] `config.py` with pydantic-settings
- [x] All 6 backend stubs
- [x] Agent orchestrator, tool definitions, guardrails
- [x] CLI skeleton (Typer + Rich)
- [x] Slack bot skeleton (Bolt)
- [x] MCP server base class + provider servers + GitHub server
- [x] FastAPI server skeleton with auth, SSE routes, MCP-over-SSE endpoint
- [x] `IMPLEMENTATION_PLAN.md`, `README.md`, `.gitignore`, `.env.example`
- [x] Test stubs for backends, agent, MCP servers

---

## Phase 0.1: Infra Scaffolding ✅

- [x] `deploy/Dockerfile`
- [x] `deploy/docker-compose.yml` — server + Loki + Prometheus + Grafana
- [x] `deploy/helm/tinker/` — Helm chart (EKS / GKE / AKS)
- [x] `deploy/terraform/aws/` — ECS Fargate module
- [x] `deploy/terraform/gcp/` — Cloud Run module
- [x] `deploy/terraform/azure/` — Container Apps module

---

## Phase 1: Backends — Real Queries ✅

Each backend returns real data from its provider.

### 1.1 CloudWatch ✅
- [x] `query_logs`, `get_metrics`, `detect_anomalies`
- [x] Log group auto-discovery, multi-log-group queries
- [ ] X-Ray trace search
- [ ] Unit tests with `moto`

### 1.2 Grafana Stack ✅
- [x] `query_logs` (LogQL), `get_metrics` (PromQL), `detect_anomalies`
- [x] `tail_logs` — native Loki websocket + poll fallback
- [ ] Tempo search API

### 1.3 GCP ✅
- [x] `query_logs` (Cloud Logging), `get_metrics` (Cloud Monitoring), `detect_anomalies`
- [ ] Cloud Trace integration

### 1.4 Azure ✅
- [x] `query_logs` (KQL), `get_metrics` (Azure Monitor), `detect_anomalies`
- [ ] App Insights dependency trace lookup

### 1.5 Datadog ✅
- [x] `query_logs`, `get_metrics`, `detect_anomalies`
- [ ] APM Traces API

### 1.6 Elasticsearch / OpenSearch ✅
- [x] `query_logs` (DSL), `get_metrics`, `detect_anomalies`

**Deliverable:** `tinker logs <service>` returns real log entries from all backends.

---

## Phase 1.5: Unified Query Language ✅

- [x] `query/ast.py` — `TextFilter`, `FieldFilter`, `AndExpr`, `OrExpr`, `NotExpr` + field aliases
- [x] `query/parser.py` — recursive-descent Lucene-lite parser
- [x] `query/resource.py` — resource type routing tables for all backends
- [x] Translators for CloudWatch, GCP, Azure/KQL, Loki/LogQL, Elasticsearch, Datadog
- [x] `--resource TYPE` flag — routes to correct log group / resource type / table / index
- [x] Cross-cloud aliases — `--resource lambda` on GCP → `cloud_function`
- [x] Raw native query passthrough
- [x] 75+ tests across all translators

**Deliverable:** `tinker logs payments-api -q 'level:ERROR'` works against every backend.

---

## Phase 2: Agent Core — Real RCA 🔄

### 2.1 Tool implementations
- [x] `query_logs`, `get_metrics`, `detect_anomalies`, `get_file`, `search_code`, `get_recent_commits`, `glob_files`
- [ ] `search_traces` — distributed trace lookup

### 2.2 Orchestrator
- [x] Agentic loop — Claude → tool calls → iterate until `end_turn`
- [x] `stream_analyze` — token-streaming for CLI `--verbose` and SSE
- [x] Model routing — `claude-sonnet-4-6` default, `claude-opus-4-6` + thinking for `--deep`
- [ ] Structured `IncidentReport` extraction from final response
- [ ] `MAX_ITERATIONS` guard

### 2.3 Prompt refinement
- [x] RCA system prompt
- [ ] Structured output prompt for reliable `IncidentReport` JSON
- [ ] Monitoring triage prompt — fast severity classification

**Deliverable:** `tinker analyze payments-api --since 1h` produces a structured incident report.

---

## Phase 3: Fix Suggestion & Guardrails 🔄

### 3.1 Fix suggestion
- [x] `suggest_fix` tool — stores diff in session, not auto-applied
- [ ] `IncidentReport.suggested_fix` populated reliably

### 3.2 Fix validation
- [ ] `FixValidator.scan(diff)` — Semgrep on changed files
- [ ] Block HIGH/CRITICAL Semgrep findings

### 3.3 Fix application
- [x] `FixApplier` skeleton — `git apply --check` before apply
- [ ] `FixApplier.create_pr(...)` — commit, push, open GitHub PR
- [ ] `tinker fix <id> --approve` — confirmation → apply → print PR URL

### 3.4 Guardrails ✅
- [x] `ApprovalRequired`, `RBACGuard`, `AuditLogger`
- [x] `sanitize_log_content` — strips credentials from log data before LLM

**Deliverable:** `tinker fix INC-001 --approve` opens a PR.

---

## Phase 4: Server + CLI ✅

### 4.1 FastAPI server
- [x] `POST /api/v1/analyze` — SSE streaming
- [x] `POST /api/v1/logs`, `POST /api/v1/metrics`, `POST /api/v1/anomalies`
- [x] `POST/GET/DELETE /api/v1/watches` — server-side watch management
- [x] `GET /health`
- [ ] `POST /api/v1/fix`, `POST /api/v1/approve`
- [ ] JWT validation via JWKS URL

### 4.2 Authentication ✅
- [x] API key validation (SHA-256 hash, constant-time comparison)
- [x] Slack request signature verification
- [x] Auth context → `actor` and `actor_roles` propagated to guardrails

### 4.3 MCP over SSE ✅
- [x] `GET /mcp/sse` + `POST /mcp/messages`
- [x] All tools from active backend exposed via single endpoint

### 4.4 `tinker server` command ✅
- [x] `tinker server` — starts uvicorn directly from the CLI
- [x] `--host`, `--port`, `--reload`, `--log-level` flags
- [x] Removed separate `tinker-server` entry point — everything through `tinker` CLI

### 4.5 Server-only CLI model ✅
- [x] Removed `LocalClient` — `RemoteClient` is the only client
- [x] `~/.tinker/config` stores server URL; `TINKER_API_TOKEN` env var stores token
- [x] `get_client()` always returns `RemoteClient`
- [x] `tinker server` on localhost = "local mode" without any special code path

**Deliverable:** `pip install tinker-agent && tinker server` starts a functional server.

---

## Phase 4.5: Live Log Streaming ✅

- [x] `tail_logs()` — `AsyncGenerator[LogEntry, None]` on all backends
- [x] Poll-based default + Loki websocket native streaming
- [x] `tinker tail <service>` CLI command

---

## Phase 4.6: Init Wizards ✅

- [x] `tinker init server` — `ServerWizard`
  - [x] Cloud auto-detection via instance metadata (AWS IMDS, GCP metadata, Azure IMDS)
  - [x] IAM/permissions check with test API call
  - [x] Slack bot token test
  - [x] API key generation + SHA-256 hash
  - [x] `.env` write
- [x] `tinker init cli` — `CLIWizard`
  - [x] Server URL prompt (default: `http://localhost:8000`)
  - [x] API token prompt
  - [x] Health check test against server
  - [x] `~/.tinker/config` write

---

## Phase 4.7: Anomaly Detection UX ✅

- [x] `tinker anomaly <service>` — fast table, no LLM
- [x] `tinker monitor <service>` — interactive REPL
  - [x] `explain <n>` — compact LLM context (~300–1000 tokens regardless of log volume)
  - [x] `fix <n>` — mini agent loop with code tools
  - [x] `approve` — apply fix and open PR
  - [x] `filter`, `refresh`, `session clean`
- [x] `LogSummarizer` — template normalisation, stack trace detection + deduplication
  - [x] Python, Java, Node, Go, Ruby stack trace patterns
  - [x] Normalised signature deduplication (IP/UUID-varying traces collapse to one)
  - [x] `build_explain_context()` — compact string for LLM prompt

---

## Phase 5: Server-side Watch System ✅

- [x] `WatchManager` — asyncio task-based, not subprocess
  - [x] Tasks persist across server restarts (loaded from SQLite on startup)
  - [x] SIGTERM-safe (tasks cancelled on server shutdown via lifespan hook)
  - [x] Anomaly hash deduplication — Slack only notified when set changes
- [x] Watch routes: `POST/GET/DELETE /api/v1/watches`
- [x] `tinker watch start/list/stop` — calls server API, no local daemon
- [x] SQLite schema updated — no PID column (server manages tasks, not OS processes)
- [x] Slack post on anomaly set change

**Deliverable:** `tinker watch start payments-api --channel "#incidents"` starts a persistent server-side watch; stops cleanly with `tinker watch stop`.

---

## Phase 6: Slack Bot 🔄

- [x] Slack Bolt mounted into FastAPI at `/slack`
- [x] `/tinker-analyze`, `/tinker-fix`, `/tinker-approve`, `/tinker-status` skeletons
- [ ] Full streaming agent output into thread
- [ ] Block Kit incident report formatter
- [ ] "Get Fix" / "Approve" / "Dismiss" action buttons
- [ ] Slack user group → role mapping
- [ ] Interactive approval flow for watch-posted alerts

**Deliverable:** Full Slack flow: watch alert → reply `explain 1` in thread → agent responds → approve from Slack.

---

## Phase 7: Hardening & Production Readiness [ ]

- [ ] OpenTelemetry instrumentation on Tinker itself
- [ ] Rate limiting on API endpoints (per-client, per-minute)
- [ ] Redis-backed session store for multi-replica deployments
- [ ] Secrets rotation — re-reads `TINKER_API_KEYS` on SIGHUP
- [ ] End-to-end integration tests: LocalStack (AWS) + docker-compose Grafana stack
- [ ] Load test: 10 concurrent `/analyze` requests
- [ ] Tests for `LogSummarizer`, `WatchManager`, `MonitorREPL`, `InitWizard`

---

## Local development environment ✅

- [x] `local-dev/docker-compose.yml` — payments-api + Loki + Prometheus + Grafana
- [x] `local-dev/dummy_server.py` — emits structured logs + Prometheus metrics
- [x] `local-dev/generate_traffic.sh` — steady / incident / burst modes
- [x] `local-dev/run.sh` — start / stop

---

## Architecture — server-only model

```
src/tinker/
├── backends/           ObservabilityBackend ABC + 6 provider implementations
├── query/              Unified query language — AST, parser, 6 translators
├── mcp_servers/        MCP wrappers — stdio (local dev) or /mcp/sse (remote)
├── server/             FastAPI app, auth, SSE routes, MCP, watch routes
│   ├── routes/
│   │   ├── agent.py    /api/v1/analyze
│   │   ├── query.py    /api/v1/logs, /metrics, /anomalies
│   │   └── watches.py  /api/v1/watches (CRUD)
│   └── watch_manager.py  asyncio task manager for background watches
├── agent/              Claude orchestrator, tool definitions, guardrails
├── interfaces/
│   ├── cli.py          tinker server, init, anomaly, monitor, watch, ...
│   ├── monitor_repl.py Interactive REPL
│   ├── init_wizard.py  ServerWizard + CLIWizard
│   └── slack_bot.py    Slack Bolt handler
├── monitor/
│   └── summarizer.py   LogSummarizer — deduplication + compact LLM context
├── client/
│   ├── remote.py       RemoteClient — HTTP to Tinker server
│   └── config.py       Reads ~/.tinker/config + TINKER_SERVER_URL
├── store/
│   └── db.py           SQLite — sessions + watch state
├── code/               Git/GitHub integration, fix application
└── config.py           Server env vars (pydantic-settings)
```

**Key design decisions:**
- No local mode — the server is always involved. `tinker server` on localhost is "local mode" with no special code path.
- Cloud credentials stay on the server (IAM role). The CLI holds only `TINKER_API_TOKEN`.
- Watches are asyncio tasks inside the server process — no detached subprocesses, no PID tracking.
- LLM cost for `monitor explain` is bounded by `LogSummarizer` regardless of raw error volume.

---

## Tech Stack

| Layer | Library |
|---|---|
| Python | 3.12+ |
| Server | fastapi, uvicorn |
| CLI | typer, rich, questionary |
| Slack | slack-bolt |
| LLM | anthropic, litellm |
| MCP | mcp |
| Auth | pyjwt[crypto] |
| AWS | boto3 |
| GCP | google-cloud-monitoring, google-cloud-logging |
| Azure | azure-identity, azure-monitor-query |
| Grafana | httpx, websockets |
| Datadog | httpx |
| Elastic | elasticsearch |
| Config | pydantic-settings |
| Logging | structlog |
| Code tools | gitpython, pygithub |
| Persistence | sqlite3 (stdlib) |
| Testing | pytest, pytest-asyncio, moto, respx |
| Packaging | uv |

---

## Definition of Done (per phase)

1. All checklist items complete
2. `uv run pytest` passes
3. `tinker` CLI smoke-test for the phase's features passes
4. No credential leaks (`trufflehog filesystem .`)
5. Structured audit log entries for every agent write action

---

## Non-goals for v1

- Auto-merging PRs — human must merge
- `tinker deploy` wizard — deployment is the platform team's job (Helm/Terraform)
- Multi-tenant / SaaS mode
- Custom model fine-tuning
- Incident ticketing integration (Jira, PagerDuty) — Phase 8+
- Kubernetes operator

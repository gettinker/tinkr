# Tinker — Implementation Plan

## Overview

Build in phases, each delivering a working vertical slice. Phases 0–2 are read-only (analyze/report). Write operations (fix/apply) come in Phase 3 after guardrails are proven. The server ships in Phase 4.

---

## Phase 0: Project Skeleton ✅

**Goal:** Runnable project with config loading, logging, and a smoke-test CLI command.

- [x] Directory structure and `pyproject.toml`
- [x] `ObservabilityBackend` ABC and data models (`LogEntry`, `MetricPoint`, `Anomaly`)
- [x] `config.py` with pydantic-settings (all env vars, fails fast on missing required)
- [x] All 6 backend stubs (`cloudwatch`, `gcp`, `azure`, `grafana`, `datadog`, `elastic`)
- [x] Agent orchestrator, tool definitions, guardrails
- [x] CLI skeleton (Typer + Rich)
- [x] Slack bot skeleton (Bolt)
- [x] MCP server base class + 6 provider servers + GitHub server
- [x] FastAPI server skeleton with auth, SSE routes, MCP-over-SSE endpoint
- [x] Deploy manifests (Dockerfile, docker-compose, ECS, Cloud Run, Container Apps)
- [x] `CLAUDE.md`, `README.md`, `.gitignore`, `.env.example`
- [x] Test stubs for backends, agent, MCP servers

**Deliverable:** `tinker version` works; all imports succeed; `docker compose up` starts the stack.

---

## Phase 1: Backends — Real Queries (Week 1–2)

**Goal:** Each backend returns real data from its provider. At least one backend passes integration tests.

### 1.1 CloudWatch (priority — most common)
- [ ] `CloudWatchBackend.query_logs` — CloudWatch Logs Insights, poll until complete
- [ ] `CloudWatchBackend.get_metrics` — GetMetricData with period/stat selection
- [ ] `CloudWatchBackend.detect_anomalies` — error count threshold + rate-of-change
- [ ] X-Ray trace search via `BatchGetTraces`
- [ ] Unit tests with `moto[logs,cloudwatch]`
- [ ] Integration test against LocalStack

### 1.2 Grafana Stack (local dev, no cloud account needed)
- [ ] `GrafanaBackend.query_logs` — Loki LogQL, label selector auto-wrapping
- [ ] `GrafanaBackend.get_metrics` — Prometheus range query, PromQL passthrough
- [ ] `GrafanaBackend.search_traces` — Tempo search API
- [ ] `GrafanaBackend.detect_anomalies` — Loki error count + Prometheus 5xx rate
- [ ] Unit tests with `respx` (httpx mocks)
- [ ] End-to-end test via `docker compose`

### 1.3 GCP
- [ ] `GCPBackend.query_logs` — Cloud Logging filter syntax, pagination
- [ ] `GCPBackend.get_metrics` — Cloud Monitoring timeseries
- [ ] `GCPBackend.detect_anomalies`
- [ ] Cloud Trace integration
- [ ] Unit tests with `pytest-mock` + VCR cassettes

### 1.4 Azure
- [ ] `AzureBackend.query_logs` — KQL query, plain-string auto-wrap
- [ ] `AzureBackend.get_metrics` — Azure Monitor Metrics, resource URI construction
- [ ] `AzureBackend.detect_anomalies` — AppExceptions summarize query
- [ ] App Insights dependency trace lookup
- [ ] Unit tests with `pytest-mock`

### 1.5 Datadog
- [ ] `DatadogBackend.query_logs` — Logs Search API v2, pagination
- [ ] `DatadogBackend.get_metrics` — Metrics query API v1, Datadog query syntax
- [ ] `DatadogBackend.search_traces` — APM Traces API v2
- [ ] `DatadogBackend.detect_anomalies`
- [ ] Unit tests with `respx`

### 1.6 Elasticsearch / OpenSearch
- [ ] `ElasticBackend.query_logs` — DSL bool query, OTel field mapping
- [ ] `ElasticBackend.get_metrics` — date_histogram aggregation
- [ ] Unit tests with VCR cassettes

**Deliverable:** `tinker logs payments-api --since 30m` returns real log entries from at least CloudWatch and Grafana.

---

## Phase 2: Agent Core — Real RCA (Week 2–3)

**Goal:** Claude analyzes a real incident end-to-end and produces a structured `IncidentReport`.

### 2.1 Tool implementations (in-process)
- [ ] `query_logs` tool — complete, with backend routing
- [ ] `get_recent_errors` tool — convenience wrapper
- [ ] `get_metrics` tool — complete
- [ ] `detect_anomalies` tool — complete
- [ ] `search_traces` tool — distributed trace lookup
- [ ] `get_file` + `search_code` + `get_recent_commits` + `blame` codebase tools

### 2.2 Orchestrator
- [ ] Full agentic loop — Claude → tool calls → results → iterate until `end_turn`
- [ ] `stream_analyze` — token-streaming for CLI `--verbose` and SSE
- [ ] `IncidentReport` populated from final agent response (structured extraction)
- [ ] Model routing — `claude-sonnet-4-6` default, `claude-opus-4-6` + thinking for `--deep`
- [ ] `MAX_ITERATIONS` guard and graceful degradation

### 2.3 Prompt refinement
- [ ] RCA system prompt — iterate on real incidents
- [ ] Structured output prompt — get reliable JSON for `IncidentReport` fields
- [ ] Monitoring triage prompt — fast severity classification

### 2.4 CLI integration
- [ ] `tinker analyze` — full RCA with Rich-formatted report
- [ ] `tinker logs` — raw log tail (no AI)
- [ ] `tinker metrics` — metric values
- [ ] `--verbose` flag streams agent reasoning

**Deliverable:** `tinker analyze payments-api --since 1h` produces a structured incident report with root cause, severity, affected services, and evidence citations.

---

## Phase 3: Fix Suggestion & Guardrails (Week 3–4)

**Goal:** Agent proposes code fixes with safety validation. Human reviews before anything is applied.

### 3.1 Fix suggestion
- [ ] `suggest_fix` tool — stores diff in session, never applies automatically
- [ ] `IncidentReport.suggested_fix` and `fix_diff` populated
- [ ] Structured diff format validated (must be proper unified diff)

### 3.2 Fix validation
- [ ] `FixValidator.scan(diff)` — Semgrep on changed files
- [ ] Block HIGH/CRITICAL findings, report MEDIUM as warnings
- [ ] Diff sanity checks (no file deletions, no binary files, max line count)

### 3.3 Fix application
- [ ] `FixApplier.apply_patch(diff)` — `git apply --check` before apply
- [ ] `FixApplier.create_pr(...)` — commit, push, open GitHub PR
- [ ] PR body template: incident ID, root cause, evidence, Semgrep results

### 3.4 Guardrails hardening
- [ ] `ApprovalRequired` — all write tools gate on `approved_tools` context key
- [ ] `RBACGuard` — role check from `actor_roles` context key
- [ ] `AuditLogger` — structlog with session ID, actor, tool, approved\_by, timestamp
- [ ] `sanitize_log_content` — regex patterns for AWS keys, Anthropic keys, Slack tokens, GH tokens

### 3.5 CLI integration
- [ ] `tinker fix <id>` — displays diff with syntax highlighting
- [ ] `tinker fix <id> --approve` — confirmation prompt → apply → print PR URL

**Deliverable:** `tinker fix INC-001 --approve` opens a PR. Semgrep blocks a deliberately insecure fix. Audit log entry written.

---

## Phase 4: Server + Remote Clients (Week 4–5)

**Goal:** Tinker runs as a server. CLI and Claude Code are remote clients.

### 4.1 FastAPI server
- [ ] `POST /api/v1/analyze` — SSE streaming, session created per request
- [ ] `POST /api/v1/fix` — return pending fix for a session
- [ ] `POST /api/v1/approve` — apply fix, requires `oncall` role in auth context
- [ ] `GET /api/v1/sessions/{id}` — session state
- [ ] `GET /health` — liveness probe

### 4.2 Authentication
- [ ] API key validation (SHA-256 hash comparison, constant-time)
- [ ] JWT validation via JWKS URL (optional SSO path)
- [ ] Slack request signature verification (`X-Slack-Signature` header)
- [ ] Auth context → `actor` and `actor_roles` propagated to guardrails

### 4.3 MCP over SSE
- [ ] `GET /mcp/sse` + `POST /mcp/messages` — MCP protocol over HTTP
- [ ] All tools from the active backend exposed via single endpoint
- [ ] `suggest_fix` available; `apply_fix` requires API-level approval
- [ ] Test with Claude Code remote MCP connection

### 4.4 Session store
- [ ] In-memory `SessionStore` with TTL eviction (current)
- [ ] Redis-backed store (optional, for multi-replica deployments)

### 4.5 CLI thin-client mode
- [ ] `TINKER_SERVER_URL` + `TINKER_API_TOKEN` env vars
- [ ] CLI sends requests to server instead of running agent locally
- [ ] Streams SSE responses and renders with Rich

**Deliverable:** Server deployed via `docker compose`. `tinker analyze` routes through the server. Claude Code connects via `/mcp/sse`.

---

## Phase 5: Slack Bot (Week 5)

**Goal:** Full Slack workflow from proactive alert through `/tinker-approve`.

### 5.1 Bot setup
- [ ] Slack Bolt mounted into FastAPI as ASGI sub-app at `/slack`
- [ ] Socket Mode support for development (no public URL needed)
- [ ] Webhook mode for production (server handles `POST /slack/events`)

### 5.2 Slash commands
- [ ] `/tinker-analyze <service> [since=1h]` — kicks off agent, streams updates into thread
- [ ] `/tinker-fix <incident-id>` — shows fix diff in thread
- [ ] `/tinker-approve <incident-id>` — checks role, calls `POST /api/v1/approve`, posts PR URL
- [ ] `/tinker-status` — active sessions count
- [ ] `/tinker-help`

### 5.3 Interactive components
- [ ] Block Kit incident report formatter (severity colour, fields, action buttons)
- [ ] "Get Fix" button → triggers fix fetch in thread
- [ ] "Approve" button → role-checked, triggers fix application
- [ ] "Dismiss" button → closes incident in session store

### 5.4 RBAC
- [ ] Fetch Slack user group memberships via `usergroups.users.list`
- [ ] Configurable group → role mapping (env var, JSON)
- [ ] Unauthorized users get a clear error message

### 5.5 Streaming updates
- [ ] Edit the initial message as the agent progresses
- [ ] Final report replaces the "analyzing..." placeholder

**Deliverable:** Full Slack flow: proactive alert posted → user runs `/tinker-analyze` → agent streams response into thread → `/tinker-approve` opens PR.

---

## Phase 6: Monitoring Loop (Week 6)

**Goal:** Tinker proactively detects anomalies without being asked.

- [ ] `MonitoringLoop` — APScheduler polls all configured services on interval
- [ ] Per-service cooldown — no re-alert within 30 min for same metric
- [ ] Severity routing — critical goes to `#incidents`, low goes to `#tinker-noise`
- [ ] Slack alert handler — `post_anomaly_alert` with action buttons
- [ ] `tinker monitor` CLI command — foreground loop with Rich live display
- [ ] Configurable alert rules per service (error rate threshold, latency p99, etc.)
- [ ] Auto-triage — monitoring loop runs a fast Claude triage to filter noise before alerting

**Deliverable:** Monitoring loop detects a simulated error spike and posts to Slack within the configured interval. Cooldown prevents duplicate alerts.

---

## Phase 7: Hardening & Production Readiness (Week 7)

- [ ] OpenTelemetry instrumentation on Tinker itself (traces + metrics)
- [ ] Rate limiting on API endpoints (per-client, per-minute)
- [ ] Redis-backed session store for multi-replica deployments
- [ ] Secrets rotation — server re-reads `TINKER_API_KEYS` on SIGHUP (no restart needed)
- [ ] End-to-end integration tests: LocalStack (AWS) + docker-compose Grafana stack
- [ ] Load test: 10 concurrent `/analyze` requests, measure time to first token
- [ ] Runbook: deploying, rotating keys, adding a new service, incident playbook

---

## Tech Stack

| Layer | Library | Version |
|---|---|---|
| Python | — | 3.12+ |
| Server | fastapi, uvicorn | ^0.111, ^0.30 |
| CLI | typer, rich | ^0.12, ^13 |
| Slack | slack-bolt | ^1.18 |
| LLM | anthropic | ^0.25 |
| MCP | mcp | ^1.0 |
| Auth | pyjwt[crypto] | ^2.8 |
| AWS | boto3 | ^1.34 |
| GCP | google-cloud-monitoring, google-cloud-logging | latest |
| Azure | azure-identity, azure-monitor-query | ^1.17, ^1.3 |
| Grafana / Prometheus | httpx | ^0.27 |
| Datadog | httpx | ^0.27 |
| Elastic | elasticsearch | ^8 |
| Config | pydantic-settings | ^2 |
| Logging | structlog | ^24 |
| Scheduling | apscheduler | ^3.10 |
| Code tools | gitpython, pygithub | ^3.1, ^2.3 |
| Testing | pytest, pytest-asyncio, moto, respx | latest |
| Packaging | uv | latest |

---

## Definition of Done (per phase)

1. All checklist items complete
2. `uv run pytest` passes with no skipped tests
3. `tinker` CLI smoke-test for the phase's features passes
4. `trufflehog filesystem .` reports no credential leaks
5. Structured audit log entries written for every agent write action

---

## Non-goals for v1

- Auto-merging PRs — human must merge
- Multi-tenant / SaaS mode
- Custom model fine-tuning
- Incident ticketing integration (Jira, PagerDuty) — Phase 8+
- Kubernetes operator

# Tinker — Implementation Plan

## Overview

Build in phases, each delivering working vertical slices. Start read-only (analyze/report), add write operations (fix/apply) only after guardrails are proven.

---

## Phase 0: Project Skeleton (Day 1)

**Goal:** Runnable project with config loading, logging, and a smoke-test CLI command.

- [x] Directory structure and `pyproject.toml`
- [x] Base classes and module stubs
- [x] `CLAUDE.md` for AI pair programming context
- [ ] `config.py` with pydantic-settings (all env vars validated at startup)
- [ ] Structured logging with structlog
- [ ] `tinker version` CLI command works end-to-end
- [ ] CI skeleton (GitHub Actions: lint + test)

**Deliverable:** `tinker version` prints version; all imports succeed; config fails fast on missing required vars.

---

## Phase 1: Observability Backends (Week 1)

**Goal:** Query logs and metrics from at least one backend.

### 1.1 Backend Abstraction
- [ ] `ObservabilityBackend` ABC with `query_logs`, `get_metrics`, `detect_anomalies`
- [ ] `LogEntry` and `MetricPoint` dataclasses
- [ ] `BackendFactory` that returns correct backend from config

### 1.2 CloudWatch Backend
- [ ] `CloudWatchBackend.query_logs` — CloudWatch Logs Insights
- [ ] `CloudWatchBackend.get_metrics` — GetMetricData API
- [ ] `CloudWatchBackend.detect_anomalies` — threshold + rate-of-change detection
- [ ] Unit tests with `moto` mocks

### 1.3 Elasticsearch Backend
- [ ] `ElasticBackend.query_logs` — Lucene/KQL query support
- [ ] `ElasticBackend.get_metrics` — aggregations
- [ ] `ElasticBackend.detect_anomalies`
- [ ] Unit tests with `pytest-elasticsearch` or VCR cassettes

### 1.4 GCP Backend
- [ ] `GCPBackend.query_logs` — Cloud Logging API
- [ ] `GCPBackend.get_metrics` — Cloud Monitoring API
- [ ] Unit tests with `google-cloud-testutils`

### 1.5 CLI integration
- [ ] `tinker logs <service> --since 1h` — raw log tail
- [ ] `tinker metrics <service> <metric>` — metric values

**Deliverable:** Can query real logs from at least CloudWatch and Elasticsearch via CLI.

---

## Phase 2: Agent Core (Week 2)

**Goal:** Claude analyzes an incident end-to-end; output is a structured incident report.

### 2.1 Tool definitions
- [ ] `query_logs` tool — wraps backend query
- [ ] `get_metrics` tool — wraps backend metrics
- [ ] `get_recent_errors` tool — convenience: last N errors for a service
- [ ] `search_traces` tool — distributed trace lookup (X-Ray / Cloud Trace)
- [ ] Tool dispatcher with input validation (pydantic)

### 2.2 Codebase tools
- [ ] `get_file` tool — fetch file from repo by path
- [ ] `search_code` tool — ripgrep over local clone
- [ ] `get_git_blame` tool — who changed what line recently
- [ ] `get_recent_commits` tool — commits touching a service in last N days

### 2.3 Orchestrator
- [ ] `AgentSession` — holds conversation history, tool state, session ID
- [ ] `Orchestrator.run(prompt)` — agentic loop: call Claude → dispatch tools → loop until done
- [ ] `IncidentReport` dataclass — severity, root_cause, affected_services, timeline, suggested_fix
- [ ] System prompt for RCA persona
- [ ] Model routing: `claude-sonnet-4-6` default, `claude-opus-4-6` for deep RCA

### 2.4 Prompts
- [ ] RCA system prompt
- [ ] Fix suggestion system prompt
- [ ] Monitoring alert triage prompt

### 2.5 CLI integration
- [ ] `tinker analyze <service>` — runs agent, prints `IncidentReport`
- [ ] `--verbose` flag streams agent thinking steps
- [ ] Rich-formatted output (tables, syntax-highlighted diffs)

**Deliverable:** `tinker analyze payments-api --since 1h` produces a structured incident report with root cause.

---

## Phase 3: Fix Suggestion & Guardrails (Week 3)

**Goal:** Agent proposes code fixes; human reviews before anything is applied.

### 3.1 Fix suggestion tool
- [ ] `suggest_fix` tool — agent produces unified diff
- [ ] Diff is stored in session, not applied
- [ ] `IncidentReport.suggested_fix` populated

### 3.2 Fix validation
- [ ] `FixValidator.scan(diff)` — runs Semgrep on proposed diff
- [ ] Blocks fixes with HIGH/CRITICAL Semgrep findings
- [ ] Reports MEDIUM findings as warnings

### 3.3 Guardrails
- [ ] `GuardRail` base class
- [ ] `ApprovalRequired` — gates destructive tools behind explicit approval
- [ ] `RBACGuard` — checks actor has required role
- [ ] `AuditLogger` — logs every tool call with actor, session, timestamp, approved_by

### 3.4 Fix applier
- [ ] `FixApplier.apply(diff, repo_path)` — applies patch to local clone
- [ ] `FixApplier.create_pr(diff, branch_name, title, body)` — opens GitHub/GitLab PR
- [ ] PR body includes incident report, root cause, and Semgrep results

### 3.5 CLI integration
- [ ] `tinker fix <incident_id>` — displays suggested fix, waits
- [ ] `tinker fix <incident_id> --approve` — applies fix and opens PR
- [ ] Confirmation prompt before `--approve` executes

**Deliverable:** `tinker fix INC-001 --approve` opens a PR with the fix, with full audit trail.

---

## Phase 4: Slack Bot (Week 4)

**Goal:** Full Slack interface with slash commands and proactive alerts.

### 4.1 Bot setup
- [ ] Slack Bolt app with Socket Mode
- [ ] Health check endpoint
- [ ] Graceful shutdown handling

### 4.2 Session management
- [ ] `SlackSession` — maps Slack thread_ts → `AgentSession`
- [ ] TTL-based session expiry (4 hours default)
- [ ] In-memory session store (Redis for production)

### 4.3 Slash commands
- [ ] `/tinker-analyze <service> [since=1h]`
- [ ] `/tinker-fix <incident_id>`
- [ ] `/tinker-approve <incident_id>`
- [ ] `/tinker-status` — lists active incidents
- [ ] `/tinker-help`

### 4.4 Slack RBAC
- [ ] Fetch user's Slack group memberships
- [ ] Map groups to `tinker` roles (config-driven)
- [ ] Block unauthorized commands with helpful error message

### 4.5 Rich Slack formatting
- [ ] Block Kit formatter for `IncidentReport`
- [ ] Severity color coding (red/orange/yellow/green)
- [ ] Inline action buttons: "Get Fix" / "Approve" / "Dismiss"
- [ ] Streaming updates via message edits as agent runs

**Deliverable:** Full Slack workflow from `/tinker-analyze` through `/tinker-approve`.

---

## Phase 5: Monitoring Loop (Week 5)

**Goal:** Tinker proactively detects and reports anomalies without being asked.

### 5.1 Anomaly detection
- [ ] `AnomalyDetector.check(service)` — polls metrics, detects threshold breaches
- [ ] Configurable rules: error rate, latency p99, 5xx rate, log error density
- [ ] Cooldown period — don't re-alert the same issue within 30 min

### 5.2 Scheduler
- [ ] APScheduler-based polling loop (configurable interval, default 60s)
- [ ] Per-service schedule configuration
- [ ] Graceful start/stop

### 5.3 Alert routing
- [ ] Route alerts to configured Slack channels by service/severity
- [ ] PagerDuty/OpsGenie webhook integration (optional)

### 5.4 CLI integration
- [ ] `tinker monitor` — starts monitoring loop in foreground
- [ ] `tinker monitor --daemon` — runs as background process

**Deliverable:** `tinker monitor` detects a simulated spike and posts to Slack within the polling interval.

---

## Phase 6: Hardening & Production Readiness (Week 6)

- [ ] OpenTelemetry instrumentation (traces + metrics on Tinker itself)
- [ ] Secrets Manager integration (AWS / GCP)
- [ ] Docker image + Kubernetes deployment manifests
- [ ] Rate limiting on Slack commands (per-user, per-channel)
- [ ] End-to-end integration tests against LocalStack (AWS) + local Elasticsearch
- [ ] Runbook / ops documentation

---

## Non-Goals (explicitly out of scope for v1)

- Auto-merging PRs (human must merge)
- Incident ticketing system integration (Jira, PagerDuty) — Phase 7+
- Multi-tenant / SaaS mode
- Training custom models

---

## Tech Stack Reference

| Layer | Library | Version |
|---|---|---|
| Python | — | 3.12+ |
| CLI | typer | ^0.12 |
| Terminal UI | rich | ^13 |
| Slack | slack-bolt | ^1.18 |
| LLM | anthropic | ^0.25 |
| AWS | boto3 | ^1.34 |
| GCP | google-cloud-monitoring, google-cloud-logging | latest |
| Elastic | elasticsearch | ^8 |
| Config | pydantic-settings | ^2 |
| Logging | structlog | ^24 |
| Scheduling | apscheduler | ^3.10 |
| Code analysis | gitpython, pygithub, semgrep | latest |
| Testing | pytest, pytest-asyncio, moto | latest |
| Packaging | uv | latest |

---

## Definition of Done (per phase)

1. All checklist items complete
2. Unit tests pass (`pytest`)
3. `tinker` CLI smoke-test for the phase's features passes
4. No secrets in code (`trufflehog` scan clean)
5. Structured audit log entries for every agent action

# CLAUDE.md — Tinker

AI coding assistant context for the Tinker codebase.

---

## Project Overview

**Tinker** is an open-source, AI-powered observability and incident response agent.
It works with every major cloud provider and self-hosted observability stack.

Core loop: detect anomaly → query logs/metrics → correlate with source code → root cause → suggest fix → human approves → PR opened.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Tinker Server  (Docker — user deploys in their own cloud)       │
│                                                                  │
│  FastAPI ──► POST /api/v1/analyze   REST + SSE streaming         │
│          ──► GET  /mcp/sse          Remote MCP for Claude Code   │
│          ──► POST /slack/events     Slack Bolt                   │
│          ──► GET  /health                                        │
│                                                                  │
│  Active backend (TINKER_BACKEND env var):                        │
│  cloudwatch | gcp | azure | grafana | datadog | elastic | otel  │
│                                                                  │
│  Credentials → cloud's native identity (IAM role / Workload     │
│  Identity / Managed Identity). Zero long-lived keys on server.  │
└──────────────────┬───────────────────────────────────────────────┘
                   │  API key (short string, never a cloud credential)
      ┌────────────┼──────────────────┐
      ▼            ▼                  ▼
   CLI          Claude Code        Slack Bot
  (thin)        remote MCP         (webhook → server)
                over SSE
```

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

---

## Deployment Model

### The server is the single point of credential trust

The Tinker server runs with **cloud-native identity** — no long-lived API keys for cloud providers:

| Cloud | Identity mechanism | Required roles |
|---|---|---|
| AWS ECS/Fargate | IAM Task Role | CloudWatch Logs read, Metrics read, X-Ray read |
| GCP Cloud Run | Workload Identity (service account) | roles/logging.viewer, roles/monitoring.viewer |
| Azure Container Apps | System-assigned Managed Identity | Monitoring Reader, Log Analytics Reader |
| On-prem / self-hosted | Grafana API key or Datadog API key in secrets manager | n/a |

The SDKs discover credentials automatically — `boto3`, `google-auth`, `DefaultAzureCredential` all check the instance metadata service. The Tinker binary has **zero credential config** for cloud backends.

**The only secret the server needs is `ANTHROPIC_API_KEY`** (plus Slack/GitHub tokens if those features are enabled). All of these go into the cloud's native secrets manager (AWS Secrets Manager / GCP Secret Manager / Azure Key Vault) and are injected as env vars at container start — they are never in source code or Docker images.

### CLI / Slack bot authenticate to the server, not to the cloud

```
Developer laptop        Tinker Server               AWS CloudWatch
     │                        │                           │
     │  Bearer <api-key>       │                           │
     ├───────────────────────► │  IAM role (automatic)    │
     │                        ├──────────────────────────►│
     │                        │◄──────────────────────────┤
     │◄───────────────────────┤                           │
```

API keys are short strings stored in `TINKER_API_KEYS` (hashed with SHA-256).
Generate one: `python -c "import secrets; print(secrets.token_urlsafe(32))"`

### Local development

```bash
docker compose -f deploy/docker-compose.yml up
# Starts Tinker server + Loki + Prometheus + Grafana UI
# TINKER_BACKEND=grafana, no cloud credentials needed
```

---

## Backends

All backends implement `ObservabilityBackend` (ABC in `src/tinker/backends/base.py`).
Selected by `TINKER_BACKEND` env var. The agent never imports a specific backend class.

| Backend | File | Logs | Metrics | Traces | Auth |
|---|---|---|---|---|---|
| `cloudwatch` | `backends/cloudwatch.py` | CloudWatch Logs Insights | GetMetricData | X-Ray | IAM role |
| `gcp` | `backends/gcp.py` | Cloud Logging | Cloud Monitoring | Cloud Trace | Workload Identity |
| `azure` | `backends/azure.py` | Log Analytics / KQL | Azure Monitor Metrics | App Insights | Managed Identity |
| `grafana` | `backends/grafana.py` | Loki / LogQL | Prometheus / PromQL | Tempo | API key / basic auth |
| `datadog` | `backends/datadog.py` | Logs API v2 | Metrics API v1 | APM Traces | API key + App key |
| `elastic` | `backends/elastic.py` | Elasticsearch DSL | Aggregations | APM | API key |
| `otel` | `backends/otel.py` | OpenSearch | Prometheus | — | API key |

### Adding a new backend

1. Create `src/tinker/backends/<name>.py` — subclass `ObservabilityBackend`
2. Implement `query_logs`, `get_metrics`, `detect_anomalies`
3. Add to `_REGISTRY` in `src/tinker/backends/__init__.py`
4. Add env vars to `src/tinker/config.py`
5. Add env vars to `.env.example` with comments
6. Create `src/tinker/mcp_servers/<name>_server.py` — subclass `TinkerMCPServer`
7. Add script entry point in `pyproject.toml`
8. Add server block to `.claude/settings.json`
9. Add deploy env var to all three cloud manifests in `deploy/`
10. Tests in `tests/test_backends/test_<name>.py`

---

## MCP Servers

### Two operating modes

**Local dev (stdio):** each backend runs as a subprocess. Claude Code spawns them directly.
**Production (remote SSE):** the Tinker server exposes `/mcp/sse`. One endpoint, all backends.

### Switching from local to remote in settings.json

Uncomment the `tinker` remote block and comment out the individual local servers:

```json
{
  "mcpServers": {
    "tinker": {
      "transport": "sse",
      "url": "https://tinker.your-company.internal/mcp/sse",
      "headers": { "Authorization": "Bearer ${TINKER_API_TOKEN}" }
    }
  }
}
```

`TINKER_API_TOKEN` is the raw API key (not the hash). Set it in your shell profile or `.env`.

### Tool naming conventions

- Local stdio servers: `<backend>_<verb>_<noun>` — e.g. `cloudwatch_query_logs`
- Remote unified server: generic names — `query_logs`, `get_metrics`, `detect_anomalies`
- Never use single-word names — they collide across servers

### MCP servers must be stateless

No session state, no in-memory caches between calls. All state lives in the agent orchestrator.

---

## Harness Best Practices

### Hooks

All hooks are in `.claude/settings.json` under `hooks`.

| Event | Matcher | Purpose |
|---|---|---|
| `PreToolUse` | `.*create_pr\|.*apply_fix` | Remind about approval before write ops |
| `PostToolUse` | `tinker.*` | Emit audit log entry |

Rules:
- Hooks must be fast — they run synchronously in the event loop
- Always end PostToolUse hooks with `|| true` — a failing hook blocks tool results
- Never put secrets in hook commands — use env var references
- Test: `echo '{}' | <hook-command>` should exit 0

### Permissions

All read-only tools are in the `allow` list in `settings.json`.
`github_create_pr` and `apply_fix` are in the `deny` list by default.
A user must explicitly allow them per-session (Claude Code will prompt).

### Secret injection into MCP servers

Use `${ENV_VAR}` substitution in `settings.json`. Set values in:
- Shell profile (`~/.zshrc`) for personal dev
- `.env` file (not committed) for project dev
- Never hardcode values in `settings.json`

---

## Key Design Decisions

### Human-in-the-loop is non-negotiable
`apply_fix` and `create_pr` require explicit approval:
- CLI: `--approve` flag + confirmation prompt
- Slack: `/tinker-approve <id>` from a user with `oncall` role
- API: `POST /api/v1/approve` requires `oncall` or `sre-lead` in JWT/API key roles
- Claude Code: `apply_fix` is in the `deny` list by default

Never bypass the `ApprovalRequired` guardrail — use `MockApproval` in tests.

### Backends are selected at server start, not per-request
`TINKER_BACKEND` is read once at startup. Users deploy one Tinker instance per cloud account.
For multi-cloud: deploy multiple instances, each with its own backend config.

### Secrets never reach the LLM
`sanitize_log_content()` in `guardrails.py` strips credentials and prompt injection patterns
from ALL data before it is included in a prompt or returned from an MCP tool.

### Model routing
- `claude-sonnet-4-6` — monitoring loop, log queries, initial triage (default)
- `claude-opus-4-6` with `thinking` — deep RCA on confirmed high-severity incidents
- Controlled in `Orchestrator` — never hardcode model names elsewhere

---

## Common Tasks

### Run the server locally

```bash
uv sync
cp .env.example .env  # fill in ANTHROPIC_API_KEY + backend vars
TINKER_BACKEND=grafana uv run tinker-server
# or with docker:
docker compose -f deploy/docker-compose.yml up
```

### Run the CLI against a deployed server

```bash
export TINKER_SERVER_URL=https://tinker.your-company.internal
export TINKER_API_TOKEN=<your-raw-api-key>
tinker analyze payments-api --since 1h
```

### Connect Claude Code to a deployed server

Edit `.claude/settings.json`:
```json
{
  "mcpServers": {
    "tinker": {
      "transport": "sse",
      "url": "https://tinker.your-company.internal/mcp/sse",
      "headers": { "Authorization": "Bearer ${TINKER_API_TOKEN}" }
    }
  }
}
```

### Test an MCP server locally

```bash
tinker-cloudwatch-mcp  # starts on stdio, send JSON-RPC manually
# Or run all servers via docker compose and hit /mcp/sse
```

### Generate and hash an API key

```bash
# Generate
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Hash (store the hash in TINKER_API_KEYS, give the raw key to the client)
python -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" <raw-key>
```

---

## Testing

```bash
uv run pytest                              # all tests
uv run pytest tests/test_backends/        # backend unit tests
uv run pytest tests/test_mcp_servers/     # MCP server tests
uv run pytest tests/test_agent/           # agent + guardrail tests
uv run pytest -k "cloudwatch"             # filter by name
```

- AWS: `moto` mocks — never hit real AWS
- GCP / Azure / Datadog: `pytest-mock` + `respx` for httpx mocks
- Grafana: httpx mock (it's all HTTP APIs)
- LLM: mock `anthropic.Anthropic` — no real API calls in tests
- Server: FastAPI `TestClient` for route tests

---

## What NOT to do

- Don't hardcode cloud region, account IDs, or project IDs anywhere
- Don't store cloud credentials (AWS keys, GCP service account JSON) in env vars on the server — use the native identity mechanism
- Don't add `--no-verify` to git commands
- Don't bypass `ApprovalRequired` guardrail outside of tests
- Don't send raw log data to the LLM without `sanitize_log_content()` first
- Don't put session state in MCP servers — they are stateless
- Don't put `github_create_pr` or `apply_fix` in the permissions `allow` list
- Don't commit `.env` files or any file containing real credentials
- Don't use `AZURE_CLIENT_SECRET` in production — use Managed Identity instead

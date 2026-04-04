# Tinker

Open-source AI-powered observability and incident response agent. Connects to your cloud backend, analyzes logs and metrics, cross-references incidents with your codebase, and suggests fixes — with human approval before any code changes.

---

## How it works

```
┌──────────────────────────────────────────────────────────────────┐
│  Tinker Server  (runs anywhere with cloud access)                │
│                                                                  │
│  tinker server  ──► FastAPI on :8000                            │
│                                                                  │
│  POST /api/v1/analyze    REST + SSE streaming RCA                │
│  POST /api/v1/anomalies  anomaly detection                       │
│  POST /api/v1/watches    server-side background watches          │
│  GET  /mcp/sse           Remote MCP for Claude Code              │
│  POST /slack/events      Slack bot                               │
│                                                                  │
│  TINKER_BACKEND=cloudwatch|gcp|azure|grafana|datadog|elastic     │
│  Credentials → IAM role / Workload Identity / Managed Identity   │
│  Zero long-lived cloud keys on the server.                       │
└──────────────────┬───────────────────────────────────────────────┘
                   │  API key  (TINKER_API_TOKEN)
      ┌────────────┼──────────────────┐
      ▼            ▼                  ▼
   CLI          Claude Code        Slack Bot
  (thin)        remote MCP         (webhook → server)
```

The server is the single point of credential trust. Cloud credentials (IAM role, Workload Identity, Managed Identity) stay on the server machine. The CLI and Slack bot authenticate with a short API token — they never touch cloud credentials.

---

## Install

```bash
pip install tinker-agent
# or
uv add tinker-agent
```

Requires Python 3.12+.

---

## Quick start

### On the server machine (EC2, Cloud Run, Azure VM, or your laptop)

```bash
# 1. Run the setup wizard — detects cloud, checks permissions, generates .env
tinker init server

# 2. Start the server
tinker server
# Listening on http://0.0.0.0:8000
# Docs: http://0.0.0.0:8000/docs
```

`tinker init server` auto-detects the cloud environment via instance metadata, verifies IAM permissions with a test API call, optionally configures Slack, generates an API key, and writes `.env`.

### On each developer's machine

```bash
# 3. Connect the CLI to the server
tinker init cli
# Tinker server URL [http://localhost:8000]: https://tinker.acme.internal
# API token: <paste key from step 1>
# ✓ Connected: Tinker v0.1.0  backend=cloudwatch

# 4. Verify
tinker doctor
```

The API token is stored as `TINKER_API_TOKEN` in your shell profile. The server URL is written to `~/.tinker/config`.

---

## Commands

All commands take a **service name** as the first argument — the service name in your observability backend (ECS service, Cloud Run service, Loki `service` label, etc.).

### Server management

```bash
tinker server                        # start on :8000
tinker server --port 9000            # custom port
tinker server --host 127.0.0.1      # bind to localhost only
tinker server --reload               # dev mode — hot reload

tinker init server                   # setup wizard for the server machine
tinker init cli                      # connect CLI to a running server

tinker doctor                        # verify server connection and backend
```

---

### Anomaly detection — `tinker anomaly`

Fast anomaly check with no LLM cost. Returns a table directly.

```bash
tinker anomaly payments-api                    # last 1h
tinker anomaly payments-api --since 2h         # custom window
tinker anomaly payments-api --severity high    # filter by severity
tinker anomaly payments-api --json             # machine-readable
```

Output shows severity, metric name, description, number of unique error patterns, and distinct stack traces detected in the error logs.

---

### Interactive monitor — `tinker monitor`

Opens a REPL session. Anomaly table is displayed immediately. LLM is only invoked when you explicitly type `explain` or `fix`.

```bash
tinker monitor payments-api
tinker monitor payments-api --since 2h
```

```
┌─ Tinker Monitor  payments-api  window: 60m ──────────────────────┐

 Anomalies — payments-api (last 60m)
 #   Severity   Metric          Description                 Patterns  Traces
 1   HIGH       error_count     847 errors in 10m           2         1
 2   MEDIUM     latency_p99     2.4s avg, threshold 1s      —         —

Commands: explain <n> · fix <n> · filter --severity high · refresh

[payments-api] >
```

#### REPL subcommands

| Command | LLM? | Description |
|---|---|---|
| `list` / `ls` | — | Re-display the anomaly table |
| `refresh` / `r` | — | Re-fetch anomalies |
| `filter --severity high` | — | Show only anomalies of given severity |
| `filter --since 30m` | — | Change the look-back window and re-fetch |
| `explain <n>` | ✓ | LLM explanation of anomaly #n |
| `fix <n>` | ✓ | LLM-proposed code fix using repo tools |
| `approve` | — | Apply the pending fix and open a GitHub PR |
| `session clean` | — | Delete sessions older than 24 h |
| `help` / `?` | — | Show command reference |
| `quit` / `q` | — | Exit |

#### LLM cost control

`explain` sends a compact summary (~300–1000 tokens) regardless of how many raw errors occurred:

- **Template normalisation** — variable parts (IPs, timestamps, UUIDs, numbers) are replaced with placeholders, so `timeout to 10.0.0.3:5432` and `timeout to 10.0.0.7:5432` collapse to one pattern
- **Stack trace extraction** — Python/Java/Node/Go/Ruby traces are detected, deduplicated by normalised signature, and trimmed to 10 lines
- **Deduplication** — identical patterns are counted, not repeated

Example: 1000 raw error logs → 2 unique patterns + 1 stack trace → 1084-char LLM context.

#### `fix` requirements

`fix <n>` searches your codebase using: `glob_files`, `get_file`, `search_code`, `get_recent_commits`, `suggest_fix`.

| Setting | How to configure |
|---|---|
| Repo path | Set `TINKER_REPO_PATH` in `.env`, or auto-detected from current git repo |
| GitHub PR | `GITHUB_TOKEN` + `GITHUB_REPO` required for `approve` |

---

### Background watches — `tinker watch`

Watches run as asyncio tasks inside the server process. The server polls for anomalies on a schedule and posts to Slack when the anomaly set changes.

```bash
# Start a watch (server-side)
tinker watch start payments-api --channel "#incidents"
tinker watch start payments-api --interval 120

# List all watches on the server
tinker watch list

# Stop a watch
tinker watch stop watch-abc123
```

```
 Server Watches
 ID               Service          Status    Channel      Interval   Last Run
 watch-a3f2b1c4   payments-api     running   #incidents   60s        2024-01-15 14:32
 watch-9e2d3b1a   auth-service     running   —            120s       2024-01-15 14:31
```

**How it works:**
1. `tinker watch start` calls `POST /api/v1/watches` on the server
2. The server starts an asyncio task that polls `detect_anomalies` every `interval` seconds
3. A SHA-256 hash of the current anomaly set is compared to the previous tick — Slack is only notified when the set changes
4. Watch state is persisted in the server's SQLite DB (`~/.tinker/tinker.db`) and resumed on server restart
5. `tinker watch stop` calls `DELETE /api/v1/watches/{id}`, cancelling the asyncio task

**Slack message format:**
```
*Tinker Watch* — `payments-api`  [watch-a3f2b1c4]

• *HIGH* `error_count` — 847 errors in 10m (threshold: 10)
• *MEDIUM* `latency_p99` — 2.4s avg (threshold: 1s)
```

Requires `SLACK_BOT_TOKEN` in `.env`.

---

### Other commands

```bash
# ── Incident analysis (full LLM RCA) ──────────────────────────────────────────
tinker analyze payments-api                        # RCA for the last hour
tinker analyze payments-api --since 2h             # look back further
tinker analyze payments-api --since 2h -v          # stream agent reasoning
tinker analyze payments-api --deep                 # extended thinking

# ── Fix (from analyze output) ─────────────────────────────────────────────────
tinker fix INC-abc123                              # show proposed fix
tinker fix INC-abc123 --approve                    # apply and open PR

# ── Stream live logs ──────────────────────────────────────────────────────────
tinker tail payments-api
tinker tail payments-api -q 'level:ERROR'
tinker tail payments-api -q 'level:(ERROR OR WARN) AND "timeout"'
tinker tail payments-api --resource ecs -q 'level:ERROR'

# ── Fetch logs (no AI) ────────────────────────────────────────────────────────
tinker logs payments-api
tinker logs payments-api -q 'level:ERROR' --since 30m
tinker logs payments-api --resource lambda -q '"cold start"'

# ── Metrics ───────────────────────────────────────────────────────────────────
tinker metrics payments-api Errors --since 2h
tinker metrics payments-api http_requests_total --resource ecs
```

---

## Query syntax

One query syntax works on every backend. Tinker translates it to CloudWatch Logs Insights, LogQL, GCP filter, KQL, Datadog search, or Elasticsearch DSL automatically.

| Pattern | Meaning |
|---|---|
| `level:ERROR` | Field match |
| `level:(ERROR OR WARN)` | Multi-value OR |
| `"connection timeout"` | Exact phrase |
| `timeout` | Substring match |
| `level:ERROR AND "timeout"` | AND |
| `NOT "health check"` | Negation |

Field aliases: `severity` → `level`, `svc`/`app` → `service`, `msg` → `message`, `trace` → `trace_id`

### Targeting infrastructure resources

Use `--resource TYPE` (or `-r TYPE`) to route queries to a specific log group / resource type. Without it each backend uses its default.

```bash
tinker logs payments-api --resource ecs -q 'level:ERROR'
tinker logs my-function  --resource lambda
tinker tail payments-api --resource eks
```

| `--resource` | CloudWatch log group | GCP resource.type | Azure table | Loki label | ES index |
|---|---|---|---|---|---|
| `lambda` | `/aws/lambda/{svc}` | `cloud_function` | `FunctionAppLogs` | `resource="lambda"` | `lambda-*` |
| `ecs` | `/ecs/{svc}` | `cloud_run_revision` | `ContainerLog` | `resource="container"` | `ecs-*` |
| `eks` / `k8s` | `/aws/containerinsights/{svc}/application` | `k8s_container` | `ContainerLog` | `resource="container"` | `kubernetes-*` |
| `ec2` / `vm` | `/aws/ec2/{svc}` | `gce_instance` | `Syslog` | `resource="host"` | `syslog-*` |
| `rds` / `db` | `/aws/rds/instance/{svc}/postgresql` | `cloudsql_database` | `AzureDiagnostics` | `resource="db"` | `rds-*` |

Cross-cloud aliases work — `--resource lambda` on GCP maps to `cloud_function`.

---

## Supported backends

| Backend | `TINKER_BACKEND` | Logs | Metrics | Auth |
|---|---|---|---|---|
| AWS CloudWatch | `cloudwatch` | Logs Insights | CloudWatch Metrics | IAM Task Role |
| GCP | `gcp` | Cloud Logging | Cloud Monitoring | Workload Identity |
| Azure | `azure` | Log Analytics / KQL | Azure Monitor | Managed Identity |
| Grafana Stack | `grafana` | Loki / LogQL | Prometheus / PromQL | API key |
| Datadog | `datadog` | Logs API v2 | Metrics API v1 | API key + App key |
| Elastic / OpenSearch | `elastic` | Elasticsearch DSL | Aggregations | API key |

---

## Supported LLM providers

Uses [LiteLLM](https://github.com/BerriAI/litellm) — swap providers by changing one env var.

| Provider | `TINKER_DEFAULT_MODEL` | Key variable |
|---|---|---|
| Anthropic | `anthropic/claude-sonnet-4-6` | `ANTHROPIC_API_KEY` |
| OpenRouter | `openrouter/anthropic/claude-sonnet-4-6` | `OPENROUTER_API_KEY` |
| OpenAI | `openai/gpt-4o` | `OPENAI_API_KEY` |
| Groq | `groq/llama-3.1-70b-versatile` | `GROQ_API_KEY` |

---

## Deployment

The simplest deployment is `pip install tinker-agent && tinker server` on any machine that has cloud access — an EC2 instance with an IAM role, a Cloud Run instance with a Workload Identity, a VM with Managed Identity, or your own laptop.

### AWS (EC2 / ECS)

```bash
# 1. Launch EC2 with an IAM role attached (see permissions below)
# 2. SSH in and:
pip install tinker-agent
tinker init server      # detects AWS automatically, verifies CloudWatch access
tinker server           # or: nohup tinker server &

# Or run as a systemd service:
# [Unit] Description=Tinker Server
# [Service] ExecStart=tinker server
#           EnvironmentFile=/etc/tinker/.env
# [Install] WantedBy=multi-user.target
```

**Required IAM permissions:**
```json
{
  "Statement": [
    { "Effect": "Allow", "Action": [
        "logs:StartQuery", "logs:GetQueryResults", "logs:DescribeLogGroups",
        "logs:FilterLogEvents", "logs:GetLogEvents"
      ], "Resource": "*" },
    { "Effect": "Allow", "Action": [
        "cloudwatch:GetMetricData", "cloudwatch:ListMetrics"
      ], "Resource": "*" }
  ]
}
```

### GCP (Cloud Run / GCE)

```bash
# Deploy as Cloud Run service with a service account bound to:
#   roles/logging.viewer  +  roles/monitoring.viewer
gcloud run deploy tinker \
  --image gcr.io/your-project/tinker-agent \
  --service-account tinker@your-project.iam.gserviceaccount.com \
  --set-env-vars TINKER_BACKEND=gcp,GCP_PROJECT_ID=your-project
```

### Azure (Container Apps / VM)

```bash
# Enable system-assigned managed identity, then assign:
#   Monitoring Reader  +  Log Analytics Reader
az role assignment create --assignee <MANAGED_IDENTITY_PRINCIPAL_ID> \
  --role "Monitoring Reader" --scope /subscriptions/SUBSCRIPTION_ID
```

### Self-hosted / Docker

```bash
cp .env.example .env      # fill in TINKER_BACKEND + credentials
docker compose -f deploy/docker-compose.yml up -d
# Starts Tinker server + Loki + Prometheus + Grafana for local testing
```

### Managing API keys

```bash
# Generate (tinker init server does this automatically)
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Hash it — store the hash in TINKER_API_KEYS, give the raw key to CLI users
python -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" <raw-key>

# TINKER_API_KEYS format in .env:
TINKER_API_KEYS='[{"hash":"<sha256>","subject":"alice","roles":["oncall"]}]'
```

---

## Claude Code (remote MCP)

Once the server is running, add it as a remote MCP server in `.claude/settings.json`:

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

Claude can then call `query_logs`, `get_metrics`, `detect_anomalies`, `search_code`, and `suggest_fix` directly from your editor.

---

## Slack bot

```
/tinker-analyze <service> since=2h
/tinker-fix INC-abc123
/tinker-approve INC-abc123          (requires oncall role)
/tinker-status
```

The bot posts proactive alerts from `tinker watch` tasks. Requires `SLACK_BOT_TOKEN` in `.env`.

---

## Configuration reference

`tinker init server` writes all of this automatically. For manual configuration:

### Server

| Variable | Description | Default |
|---|---|---|
| `TINKER_BACKEND` | Active backend | `cloudwatch` |
| `ANTHROPIC_API_KEY` | or `OPENROUTER_API_KEY` / `OPENAI_API_KEY` / `GROQ_API_KEY` | — |
| `TINKER_DEFAULT_MODEL` | Model for triage | `anthropic/claude-sonnet-4-6` |
| `TINKER_DEEP_RCA_MODEL` | Model for `--deep` | `anthropic/claude-opus-4-6` |
| `TINKER_API_KEYS` | JSON array of hashed keys | `[]` |
| `TINKER_SERVER_PORT` | Bind port | `8000` |
| `TINKER_SERVER_HOST` | Bind host | `0.0.0.0` |
| `TINKER_REPO_PATH` | Path to service codebase for `fix` | — |

### CLI

| File / Variable | Description |
|---|---|
| `~/.tinker/config` | Server URL — written by `tinker init cli` |
| `TINKER_SERVER_URL` | Override server URL (env var) |
| `TINKER_API_TOKEN` | API token — add to shell profile |

### Per-backend

| Backend | Variables |
|---|---|
| `cloudwatch` | `AWS_REGION` — credentials from IAM role |
| `gcp` | `GCP_PROJECT_ID` — credentials from Workload Identity |
| `azure` | `AZURE_WORKSPACE_ID`, `AZURE_SUBSCRIPTION_ID` |
| `grafana` | `GRAFANA_LOKI_URL`, `GRAFANA_PROMETHEUS_URL` |
| `datadog` | `DATADOG_API_KEY`, `DATADOG_APP_KEY`, `DATADOG_SITE` |
| `elastic` | `ELASTICSEARCH_URL`, `ELASTICSEARCH_API_KEY` |

See [.env.example](.env.example) for the full reference.

---

## Security

| Concern | How Tinker handles it |
|---|---|
| Cloud credentials | Never on the CLI — server uses cloud-native identity |
| Client auth | Short API tokens, SHA-256 hashed at rest |
| Destructive operations | `apply_fix` and `create_pr` require explicit human approval |
| RBAC | Slack commands gated by role mapping |
| Prompt injection | Log content sanitized before inclusion in any LLM prompt |
| Secrets in logs | Credentials stripped from all log data before LLM submission |

---

## Local development

The [`local-dev/`](local-dev/) directory runs a full observability stack locally with no cloud account.

```bash
# 1. Start Loki + Prometheus + Grafana + a dummy payments-api
cd local-dev && ./run.sh

# 2. Start Tinker server (separate terminal)
cp .env.example .env
# Set: TINKER_BACKEND=grafana, GRAFANA_LOKI_URL=http://localhost:3100, ANTHROPIC_API_KEY=...
tinker server

# 3. Point CLI at it
tinker init cli   # URL: http://localhost:8000

# 4. Generate traffic and query
./generate_traffic.sh incident
tinker anomaly payments-api --since 5m
tinker monitor payments-api
```

---

## Development

```bash
uv sync
uv run pytest                    # all tests
uv run pytest tests/test_query/  # query translator tests
uv run ruff check src/
uv run mypy src/
```

---

## License

MIT

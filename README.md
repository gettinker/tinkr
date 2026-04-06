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
│  Active profile → backend (cloudwatch|gcp|azure|grafana|…)      │
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
# Install globally — tinker available in your PATH everywhere, no venv needed
uv tool install tinker-agent

# Or with pip
pip install --user tinker-agent
```

Requires Python 3.12+.

> **Development install** (editable, from source):
> ```bash
> git clone https://github.com/your-org/tinker
> cd tinker
> uv sync                       # creates .venv + installs all deps
> uv tool install --editable .  # installs tinker globally as editable
> ```

---

## Quick start

### On the server machine (EC2, Cloud Run, Azure VM, or your laptop)

```bash
# 1. Run the setup wizard
tinker init server
#   Wizard order:
#   Step 1 — LLM provider + model + API key
#   Step 2 — Slack bot (optional)
#   Step 3 — GitHub integration (fix + approve)
#   Step 4 — Server API key (for CLI auth)
#   Step 5 — Profiles: cloud backend + services + notifiers (loops for multi-cloud)
#
#   Writes: ~/.tinker/config.toml  (structure)
#           ~/.tinker/.env         (secrets)

# 2. Start the server
tinker server
# Listening on http://0.0.0.0:8000
```

### On each developer's machine

```bash
# 3. Connect the CLI to the server
tinker init cli
# Tinker server URL [http://localhost:8000]: https://tinker.acme.internal
# API token: <paste key from step 1>
# ✓ Connected: Tinker v0.1.0  backend=cloudwatch
# ✓ Saved: ~/.tinker/config

# 4. Verify
tinker doctor
```

---

## Commands

All observability commands take a **service name** as the first argument — the service name in your observability backend (ECS service, Cloud Run service, Loki `service` label, etc.).

### Server management

```bash
tinker server                        # start on :8000
tinker server --port 9000            # custom port
tinker server --host 127.0.0.1       # bind to localhost only
tinker server --reload               # dev mode — hot reload

tinker init server                   # first-time setup wizard
tinker init cli                      # connect CLI to a running server

tinker doctor                        # verify server connection and backend
```

---

### Profile management — `tinker profile`

A **profile** bundles a cloud backend with its services and alert notifiers. Use one profile per cloud account. The active profile is what the server uses.

```bash
tinker profile list                  # show all profiles + which is active
tinker profile use aws-prod          # switch active profile
tinker profile add                   # add a new profile interactively
```

```
 Profiles
   Name          Backend       Services   Notifiers
 ● aws-prod      cloudwatch       3           2
 ○ aws-staging   cloudwatch       1           1
 ○ local-dev     grafana          2           1

Active: aws-prod — change with tinker profile use <name>
```

`tinker profile use` updates `active_profile` in `~/.tinker/config.toml` immediately. The server picks it up on the next restart (or `tinker server --reload`).

---

### Anomaly detection — `tinker anomaly`

Fast anomaly check with no LLM cost. Returns a table directly.

```bash
tinker anomaly payments-api                    # last 1h
tinker anomaly payments-api --since 2h         # custom window
tinker anomaly payments-api --severity high    # filter by severity
tinker anomaly payments-api --output json      # machine-readable
```

Output shows severity, metric name, description, number of unique error patterns, and distinct stack traces detected in the error logs.

---

### Interactive investigation — `tinker investigate`

Log-driven end-to-end debugging: fetch errors → group by pattern → explain → fix → PR. LLM is only invoked when you explicitly type `explain` or `fix`.

```bash
tinker investigate payments-api
tinker investigate payments-api --since 2h
tinker investigate payments-api --level WARN
```

**Level 1 — error groups:**

```
 Error Groups — payments-api (last 30m, level=ERROR)
 #   Level   Count   Pattern                                      Traces   First seen
 1   ERROR     847   DB connection timeout to <ip>:<n> after <n>     3     11:02:15
 2   ERROR      12   NullPointerException in PaymentService           1     11:15:43
 3   ERROR     234   HTTP 503 from inventory-service                  0     11:01:00

Commands: logs <n> · explain <n> · fix <n> · filter --since 30m · refresh
```

**Level 2 — drill into a group (`logs 1`):**

```
 Log entries (group #1 · 847 occurrences)
 #   Time       Level   Message
 1   11:02:15   ERROR   DB connection timeout to 10.0.0.3:5432 after 30s
 2   11:02:17   ERROR   DB connection timeout to 10.0.0.7:5432 after 30s
 ...

Commands: explain · fix · back
```

#### REPL commands

| Command | LLM? | Description |
|---|---|---|
| `list` / `ls` | — | Re-display current view (groups or entries) |
| `refresh` / `r` | — | Re-fetch logs and regroup |
| `filter --since 30m` | — | Change look-back window and re-fetch |
| `filter --level WARN` | — | Switch level filter (ERROR / WARN / ALL) |
| `logs <n>` | — | Drill into group #n — show individual entries + stack traces |
| `back` / `b` | — | Return to groups view |
| `explain <n>` | ✓ | AI explains group #n — shows error classification first |
| `fix <n>` | ✓ | AI proposes code fix (skipped for transient errors) |
| `approve` | — | Apply the pending fix and open a GitHub PR |
| `session clean` | — | Delete sessions older than 24 h |
| `help` / `?` | — | Show command reference |
| `quit` / `q` | — | Exit |

#### Error classification

`explain` shows the classification before the AI narrative:

```
Classification: logic_bug

Root cause: NullPointerException occurs in PaymentService.processRefund()
when order.getCustomer() returns null for guest checkouts...
```

Types: `transient` · `logic_bug` · `config_error` · `dependency_down`

For `transient` errors, `fix` prints an analysis without generating a code patch.

#### LLM cost control

`explain` sends a compact summary (~300–1000 tokens) regardless of how many raw errors occurred:

- **Template normalisation** — variable parts (IPs, timestamps, UUIDs, numbers) are replaced with placeholders so `timeout to 10.0.0.3:5432` and `timeout to 10.0.0.7:5432` collapse to one pattern
- **Stack trace deduplication** — Python/Java/Node/Go/Ruby traces are detected, deduplicated by signature, trimmed to 30 lines
- **Representative sampling** — one example log per unique pattern, preferring entries that contain a stack trace

Example: 1000 raw error logs → 2 unique patterns + 1 stack trace → ~1000-token LLM context.

#### `fix` requirements

| Setting | How to configure |
|---|---|
| GitHub repo | Configure in `[profiles.*].services.<name>.repo` or `[github].default_repo` |
| GitHub token | `GITHUB_TOKEN` in `~/.tinker/.env` |

---

### Background watches — `tinker watch`

Watches run as asyncio tasks inside the server process. The server polls for anomalies on a schedule and dispatches alerts via the configured notifier when the anomaly set changes.

```bash
# Start a watch — uses the "default" notifier from the active profile
tinker watch start payments-api
tinker watch start payments-api --interval 120

# Route alerts to a specific notifier
tinker watch start payments-api --notifier discord-ops
tinker watch start payments-api --notifier slack-main --destination "#payments-oncall"

# List all watches on the server
tinker watch list

# Stop a watch
tinker watch stop watch-abc123
```

```
 Server Watches
 ID               Service          Status    Notifier      Interval   Last Run
 watch-a3f2b1c4   payments-api     running   default        60s        2024-01-15 14:32
 watch-9e2d3b1a   auth-service     running   discord-ops   120s       2024-01-15 14:31
```

**How it works:**
1. `tinker watch start` calls `POST /api/v1/watches` on the server
2. The server starts an asyncio task that polls `detect_anomalies` every `interval` seconds
3. A SHA-256 hash of the current anomaly set is compared to the previous tick — the notifier is only called when the set changes
4. Watch state is persisted in SQLite (`~/.tinker/tinker.db`) and resumed on server restart
5. `tinker watch stop` calls `DELETE /api/v1/watches/{id}`, cancelling the asyncio task

**Alert message format (Slack):**
```
*Tinker Watch* — `payments-api`  [watch-a3f2b1c4]

• *HIGH* `error_count` — 847 errors in 10m (threshold: 10)
• *MEDIUM* `latency_p99` — 2.4s avg (threshold: 1s)
```

Notifiers are configured per profile — see [Profiles and notifiers](#profiles-and-notifiers) below.

---

### Other commands

```bash
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

Use `--resource TYPE` (or `-r TYPE`) to route queries to a specific log group / resource type.

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

| Backend | `backend` value | Logs | Metrics | Auth |
|---|---|---|---|---|
| AWS CloudWatch | `cloudwatch` | Logs Insights | CloudWatch Metrics | IAM Task Role |
| GCP | `gcp` | Cloud Logging | Cloud Monitoring | Workload Identity |
| Azure | `azure` | Log Analytics / KQL | Azure Monitor | Managed Identity |
| Grafana Stack | `grafana` | Loki / LogQL | Prometheus / PromQL | API key |
| Datadog | `datadog` | Logs API v2 | Metrics API v1 | API key + App key |
| Elastic / OpenSearch | `elastic` | Elasticsearch DSL | Aggregations | API key |

---

## Supported LLM providers

Uses [LiteLLM](https://github.com/BerriAI/litellm) — swap providers by changing one config value.

| Provider | `default_model` | Key variable |
|---|---|---|
| Anthropic | `anthropic/claude-sonnet-4-6` | `ANTHROPIC_API_KEY` |
| OpenRouter | `openrouter/anthropic/claude-sonnet-4-6` | `OPENROUTER_API_KEY` |
| OpenAI | `openai/gpt-4o` | `OPENAI_API_KEY` |
| Groq | `groq/llama-3.1-70b-versatile` | `GROQ_API_KEY` |

Set via `[llm]` in `config.toml` (wizard sets this in Step 1).

---

## Deployment

The simplest deployment is `pip install tinker-agent && tinker server` on any machine that has cloud access — an EC2 instance with an IAM role, a Cloud Run instance with a Workload Identity, or your laptop.

### AWS (EC2 / ECS)

```bash
# 1. Launch EC2 with an IAM role attached (see permissions below)
# 2. SSH in and:
pip install tinker-agent
tinker init server      # detects AWS automatically, verifies CloudWatch access
tinker server           # or: nohup tinker server &
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
gcloud run deploy tinker \
  --image gcr.io/your-project/tinker-agent \
  --service-account tinker@your-project.iam.gserviceaccount.com \
  --set-env-vars TINKER_BACKEND=gcp,GCP_PROJECT_ID=your-project
```

Required roles: `roles/logging.viewer` + `roles/monitoring.viewer`

### Azure (Container Apps / VM)

```bash
az role assignment create --assignee <MANAGED_IDENTITY_PRINCIPAL_ID> \
  --role "Monitoring Reader" --scope /subscriptions/SUBSCRIPTION_ID
az role assignment create --assignee <MANAGED_IDENTITY_PRINCIPAL_ID> \
  --role "Log Analytics Reader" --scope /subscriptions/SUBSCRIPTION_ID
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

# Hash it — store the hash in config.toml [auth], give the raw key to CLI users
python -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" <raw-key>
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

## GitHub integration (code investigation + auto-PRs)

The `fix` and `approve` commands require a GitHub token so the server can read your code and open PRs — no local clone needed.

### 1. Create a token

Go to **GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens**.

Required scopes: `Contents` read, `Commits` read, `Pull requests` write, `Metadata` read.

### 2. Add to server config

`tinker init server` asks for this interactively (Step 3). For manual setup:

```bash
# ~/.tinker/.env
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
```

```toml
# ~/.tinker/config.toml
[github]
token = "env:GITHUB_TOKEN"
default_repo = "acme/monorepo"
```

Per-service repos are configured inside each profile (wizard Step 5, or by editing `config.toml`):

```toml
[profiles.aws-prod.services.payments-api]
repo = "acme/payments"

[profiles.aws-prod.services.auth-service]
repo = "acme/auth"
```

### Bitbucket / GitLab

Native support is not yet built in. Workaround: mirror repos to GitHub and point `default_repo` there.

---

## Profiles and notifiers

### Profiles

A profile bundles a backend with its services and notifiers. Use one per cloud account.

```toml
# ~/.tinker/config.toml

active_profile = "aws-prod"   # which profile is currently active

[profiles.aws-prod]
backend = "cloudwatch"
region  = "us-east-1"

  [profiles.aws-prod.services.payments-api]
  repo          = "acme/payments"
  resource_type = "ecs"

  [profiles.aws-prod.services.auth-service]
  resource_type = "lambda"

  [profiles.aws-prod.notifiers.default]
  type    = "slack"
  bot_token = "env:SLACK_BOT_TOKEN"
  channel = "#prod-incidents"

[profiles.aws-staging]
backend = "cloudwatch"
region  = "eu-west-1"

  [profiles.aws-staging.notifiers.default]
  type    = "slack"
  bot_token = "env:SLACK_BOT_TOKEN"
  channel = "#staging-alerts"

[profiles.local-dev]
backend          = "grafana"
loki_url         = "http://localhost:3100"
prometheus_url   = "http://localhost:9090"
api_key          = "env:GRAFANA_API_KEY"
```

**Profile commands:**

```bash
tinker profile list              # show profiles + active marker
tinker profile use aws-staging   # switch active profile (updates config.toml)
tinker profile add               # wizard to add a new profile
```

### Notifiers

Notifiers live inside their profile. Notifier type options:

**Slack:**
```toml
[profiles.aws-prod.notifiers.default]
type      = "slack"
bot_token = "env:SLACK_BOT_TOKEN"
channel   = "#incidents"

[profiles.aws-prod.notifiers.payments-team]
type      = "slack"
bot_token = "env:SLACK_BOT_TOKEN"
channel   = "#payments-oncall"
```

**Discord:**
```toml
[profiles.aws-prod.notifiers.discord-ops]
type        = "discord"
webhook_url = "env:DISCORD_OPS_WEBHOOK_URL"
```

```bash
DISCORD_OPS_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

**Generic webhook (PagerDuty, custom receivers, etc.):**
```toml
[profiles.aws-prod.notifiers.pagerduty]
type                 = "webhook"
url                  = "env:PAGERDUTY_WEBHOOK_URL"
header_Authorization = "env:PAGERDUTY_API_KEY"
```

Webhook payload:
```json
{
  "watch_id": "watch-a3f2b1c4",
  "service": "payments-api",
  "anomaly_count": 2,
  "anomalies": [
    { "metric": "error_count", "severity": "high", "description": "847 errors in 10m" }
  ]
}
```

**Using notifiers in watches:**
```bash
tinker watch start payments-api                                      # uses "default"
tinker watch start payments-api --notifier discord-ops               # named notifier
tinker watch start payments-api --notifier default --destination "#payments-oncall"
```

---

## Slack bot

### 1. Create a Slack app

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. Under **OAuth & Permissions** → **Bot Token Scopes**, add: `chat:write`, `channels:read`, `commands`
3. Under **Slash Commands**, create: `/tinker-logs`, `/tinker-anomaly`, `/tinker-analyze`, `/tinker-fix`, `/tinker-approve`, `/tinker-watch`, `/tinker-status`, `/tinker-help`
4. Under **Event Subscriptions** → enable, set Request URL to `https://tinker.your-company.internal/slack/events`
5. **Install to workspace** → copy the **Bot User OAuth Token** (`xoxb-...`)
6. Copy the **Signing Secret** from **Basic Information**

### 2. Add to server config

`tinker init server` asks for these interactively (Step 2). For manual setup:

```bash
# ~/.tinker/.env
SLACK_BOT_TOKEN=xoxb-xxxxxxxxxxxx-xxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxxxxx
SLACK_SIGNING_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

```toml
# ~/.tinker/config.toml
[slack]
bot_token      = "env:SLACK_BOT_TOKEN"
alerts_channel = "#incidents"
```

### 3. Usage

```
/tinker-logs payments-api since=30m q=level:ERROR
/tinker-anomaly payments-api since=1h severity=high
/tinker-analyze payments-api since=2h
/tinker-watch start payments-api interval=120
/tinker-watch list
/tinker-watch stop watch-abc123
/tinker-approve INC-abc123
/tinker-status
/tinker-help
```

---

## Configuration reference

`tinker init server` writes all of this automatically.

| File | Purpose |
|---|---|
| `~/.tinker/config.toml` | Structure — profiles, LLM, Slack, GitHub, auth, server settings |
| `~/.tinker/.env` | Secrets — API keys, tokens. Never commit this file |

Secrets are referenced in `config.toml` as `"env:VAR_NAME"` and resolved at server startup.

### Full `~/.tinker/config.toml` structure

```toml
# Which profile the server uses
active_profile = "aws-prod"

[server]
host      = "0.0.0.0"
port      = 8000
log_level = "info"

[llm]
default_model  = "anthropic/claude-sonnet-4-6"
deep_rca_model = "anthropic/claude-opus-4-6"

[auth]
api_keys = [{hash = "<sha256>", subject = "alice", roles = ["oncall"]}]

[slack]
bot_token      = "env:SLACK_BOT_TOKEN"
alerts_channel = "#incidents"

[github]
token        = "env:GITHUB_TOKEN"
default_repo = "acme/monorepo"

# ── Profiles ──────────────────────────────────────────────────────────────────

[profiles.aws-prod]
backend = "cloudwatch"
region  = "us-east-1"

  [profiles.aws-prod.notifiers.default]
  type      = "slack"
  bot_token = "env:SLACK_BOT_TOKEN"
  channel   = "#prod-incidents"

  [profiles.aws-prod.services.payments-api]
  repo          = "acme/payments"
  resource_type = "ecs"
  log_format    = "json"

[profiles.local-dev]
backend        = "grafana"
loki_url       = "http://localhost:3100"
prometheus_url = "http://localhost:9090"
api_key        = "env:GRAFANA_API_KEY"

  [profiles.local-dev.notifiers.default]
  type        = "discord"
  webhook_url = "env:DISCORD_DEV_WEBHOOK_URL"
```

### `~/.tinker/.env` (secrets only)

```bash
# DO NOT COMMIT
ANTHROPIC_API_KEY=sk-ant-...
SLACK_BOT_TOKEN=xoxb-...
GITHUB_TOKEN=ghp_...
GRAFANA_API_KEY=glsa_...
DISCORD_DEV_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

### CLI config files

| File / Variable | Description |
|---|---|
| `~/.tinker/config` | Server URL + API token — written by `tinker init cli` |
| `~/.tinker/.env` | Server secrets — written by `tinker init server`, auto-loaded by `tinker server` |
| `~/.tinker/config.toml` | Server structure config — written by `tinker init server` |
| `~/.tinker/tinker.db` | SQLite — REPL sessions, watch state |
| `TINKER_SERVER_URL` | Override server URL (env var takes priority over `~/.tinker/config`) |
| `TINKER_API_TOKEN` | Override API token (env var takes priority over `~/.tinker/config`) |

### Fallback: `.env`-only mode

If `config.toml` does not exist, the server falls back to env var configuration:

| Variable | Description | Default |
|---|---|---|
| `TINKER_BACKEND` | Active backend | `cloudwatch` |
| `ANTHROPIC_API_KEY` | or `OPENROUTER_API_KEY` / `OPENAI_API_KEY` / `GROQ_API_KEY` | — |
| `TINKER_API_KEYS` | JSON array of hashed keys | `[]` |
| `TINKER_SERVER_PORT` | Bind port | `8000` |
| `TINKER_SERVER_HOST` | Bind host | `0.0.0.0` |

See [.env.example](.env.example) for the full env var reference.

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
# 1. Start Loki + Prometheus + Grafana + dummy payments-api
cd local-dev && ./run.sh

# 2. Configure and start Tinker server (separate terminal)
tinker init server
#   Step 1 → pick Anthropic, enter ANTHROPIC_API_KEY
#   Step 5 → pick "Self-hosted (Grafana)", enter Loki/Prometheus URLs
tinker server

# 3. Point CLI at it
tinker init cli    # URL: http://localhost:8000

# 4. Generate traffic and query
cd local-dev && ./generate_traffic.sh incident
tinker anomaly payments-api --since 5m
tinker investigate payments-api
```

---

## Development

```bash
git clone https://github.com/your-org/tinker && cd tinker
uv sync                          # create .venv, install all deps

# Run via venv (no global install needed during dev)
uv run tinker --help
uv run tinker server

# Install globally as editable (changes in src/ take effect immediately)
uv tool install --editable .
tinker --help

# Tests
uv run pytest                    # all tests
uv run pytest tests/test_query/  # query translator tests
uv run ruff check src/
uv run mypy src/
```

All per-user state lives in `~/.tinker/`:

| File | Written by | Used by |
|---|---|---|
| `~/.tinker/config.toml` | `tinker init server` | `tinker server` (structure + routing) |
| `~/.tinker/.env` | `tinker init server` | `tinker server` (secrets) |
| `~/.tinker/config` | `tinker init cli` | all CLI commands |
| `~/.tinker/tinker.db` | auto-created | `tinker investigate`, `tinker watch` |

---

## License

MIT

# Tinkr

Open-source AI-powered observability and incident response agent. Connects to your cloud backend, analyzes logs and metrics, cross-references incidents with your codebase, and suggests fixes — with human approval before any code changes.

---

## How it works

```
┌──────────────────────────────────────────────────────────────────┐
│  Tinkr Server   (runs anywhere with cloud access)                │
│                                                                  │
│  tinkr-server  ──► FastAPI on :8000                             │
│                                                                  │
│  POST /api/v1/rca        full AI root-cause analysis (SSE)       │
│  POST /api/v1/anomalies  anomaly detection                       │
│  POST /api/v1/traces     distributed trace fetch                 │
│  POST /api/v1/slo        SLO / error budget computation          │
│  POST /api/v1/watches    server-side background watches          │
│  POST /api/v1/alerts     threshold-based alert rules             │
│  GET  /api/v1/deploys    GitHub commit / deploy history          │
│  GET  /mcp/sse           Remote MCP for Claude Code              │
│  POST /slack/events      Slack bot                               │
│                                                                  │
│  Active profile → backend (cloudwatch|gcp|azure|grafana|…)       │
│  Credentials → IAM role / Workload Identity / Managed Identity   │
│  Zero long-lived cloud keys on the server.                       │
└───────────────────────────────┬──────────────────────────────────┘
                                │  API key  (TINKR_API_TOKEN)
                   ┌────────────┼──────────────────┐
                   ▼            ▼                  ▼
                  CLI          Claude Code        Slack Bot
                 (thin)        Remote MCP         (webhook → server)
```

The server is the single point of credential trust. Cloud credentials (IAM role, Workload Identity, Managed Identity) stay on the server machine. The CLI and Slack bot authenticate with a short API token — they never touch cloud credentials.

---

## Install

```bash
# uv (recommended)
uv tool install tinkr

# pipx
pipx install tinkr

# pip
pip install tinkr
```

> **macOS / externally managed Python:** use `uv tool install` or `pipx install` — no `sudo` or virtualenv activation needed.

### Build from source

```bash
git clone https://github.com/gettinker/tinkr
uv sync                       # creates .venv + installs all deps
uv tool install --editable .  # installs tinkr globally as editable
```

### Docker

```bash
git clone https://github.com/gettinker/tinkr
docker build -t tinker:local .
docker run -d -p 8000:8000 --env-file ~/.tinkr/.env -v ~/.tinkr:/root/.tinkr tinker:local
```

---

## Quick start

### On the server machine (EC2, Cloud Run, Azure VM, or your laptop)

```bash
# 1. Run the setup wizard
tinkr-server init
#   Wizard order:
#   Step 1 — LLM provider + model + API key
#   Step 2 — Slack bot (optional)
#   Step 3 — GitHub integration (fix + approve)
#   Step 4 — Server API key (for CLI auth)
#   Step 5 — Profiles: cloud backend + services + notifiers (loops for multi-cloud)
#
#   Writes: ~/.tinkr/config.toml  (structure)
#           ~/.tinkr/.env         (secrets)

# 2. Start the server
tinkr-server start
# Listening on http://0.0.0.0:8000
```

### On each developer's machine

```bash
# 3. Connect the CLI to the server
tinkr init
# Tinker server URL [http://localhost:8000]: https://tinker.acme.internal
# API token: <paste key from step 1>
# ✓ Connected: Tinkr v0.1.0  backend=cloudwatch
# ✓ Saved: ~/.tinkr/config

# 4. Verify
tinkr doctor
```

---

## Commands

All observability commands take a **service name** as the first argument — the service name in your observability backend (ECS service, Cloud Run service, Loki `service` label, etc.).

### Server management

```bash
tinkr-server                        # start on :8000
tinkr-server start --port 9000            # custom port
tinkr-server start --host 127.0.0.1       # bind to localhost only
tinkr-server start --reload               # dev mode — hot reload

tinkr-server init                   # first-time setup wizard
tinkr init                      # connect CLI to a running server

tinkr doctor                        # verify server connection and backend
```

### Command summary

| Command | LLM? | Description |
|---|---|---|
| `tinkr logs <svc>` | — | Fetch recent log entries |
| `tinkr tail <svc>` | — | Stream live logs (Ctrl-C to stop) |
| `tinkr metrics <svc> <metric>` | — | Fetch metric time series |
| `tinkr anomaly <svc>` | — | Detect anomalies (fast, no LLM) |
| `tinkr trace <svc>` | — | Fetch recent distributed traces |
| `tinkr diff <svc>` | — | Compare two time windows side-by-side |
| `tinkr slo <svc>` | — | Availability, error budget, and burn rate |
| `tinkr investigate <svc>` | on demand | Interactive REPL — group, explain, fix, PR |
| `tinkr rca <svc>` | ✓ | Full streaming root-cause analysis |
| `tinkr deploy list <svc>` | — | Recent commits (deploys) from GitHub |
| `tinkr deploy correlate <svc>` | — | Highlight deploys near anomaly spikes |
| `tinkr watch start/list/stop/delete` | — | Server-side background anomaly watches |
| `tinkr alert create/list/mute/delete` | — | Threshold-based alert rules |
| `tinkr profile list/use/add` | — | Manage cloud backend profiles |

---

### Profile management — `tinkr profile`

A **profile** bundles a cloud backend with its services and alert notifiers. Use one profile per cloud account. The active profile is what the server uses.

```bash
tinkr profile list                  # show all profiles + which is active
tinkr profile use aws-prod          # switch active profile
tinkr profile add                   # add a new profile interactively
```

```
 Profiles
   Name          Backend       Services   Notifiers
 ● aws-prod      cloudwatch       3           2
 ○ aws-staging   cloudwatch       1           1
 ○ local-dev     grafana          2           1

Active: aws-prod — change with tinkr profile use <name>
```

`tinkr profile use` updates `active_profile` in `~/.tinkr/config.toml` immediately. The server picks it up on the next restart (or `tinkr-server start --reload`).

---

### Anomaly detection — `tinkr anomaly`

Fast anomaly check with no LLM cost. Returns a table directly.

```bash
tinkr anomaly payments-api                    # last 1h
tinkr anomaly payments-api --since 2h         # custom window
tinkr anomaly payments-api --severity high    # filter by severity
tinkr anomaly payments-api --output json      # machine-readable
```

Output shows severity, metric name, description, number of unique error patterns, and distinct stack traces detected in the error logs.

---

### Interactive investigation — `tinkr investigate`

Log-driven end-to-end debugging: fetch errors → group by pattern → explain → fix → PR. LLM is only invoked when you explicitly type `explain` or `fix`.

```bash
tinkr investigate payments-api
tinkr investigate payments-api --since 2h
tinkr investigate payments-api --level WARN
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
| GitHub token | `GITHUB_TOKEN` in `~/.tinkr/.env` |

---

### Distributed tracing — `tinkr trace`

Fetch recent traces from your tracing backend (Tempo, X-Ray, Cloud Trace, Datadog APM). Backends that don't support tracing return an empty list gracefully.

```bash
tinkr trace payments-api                    # last 1h
tinkr trace payments-api --since 30m        # shorter window
tinkr trace payments-api --limit 50         # more results
tinkr trace payments-api --output json
```

```
 Traces — payments-api
 Trace ID     Operation                    Duration   Spans   Status   Started
 a1b2c3d4e5f6 POST /api/v1/charge           1 247ms      12   error    14:01:03
 9e8d7c6b5a4f GET  /api/v1/orders              42ms       4   ok       14:01:01
 3f2e1d0c9b8a POST /api/v1/refund            3 891ms      18   error    14:00:58

Tip: check your tracing backend (Tempo / X-Ray / Cloud Trace) for the full waterfall.
```

---

### Window diff — `tinkr diff`

Compare error rates and anomalies between two time windows. Useful for answering "is this worse than it was an hour ago?"

The baseline window is automatically shifted back so it ends where the compare window begins — windows never overlap.

```bash
tinkr diff payments-api                             # baseline=2h vs now=1h
tinkr diff payments-api --baseline 24h --compare 1h
tinkr diff auth-service --output json
```

```
 Window Diff — payments-api
 Metric           Baseline (2h)   Now (1h)   Delta
 Error count            84            312     ▲ +228
 Anomaly count           1              3     ▲ +2
 Severity score          2              8     ▲ +6

New anomalies (2):
  • HIGH latency_p99 — 3.2s avg (threshold: 1s)
  • MEDIUM db_connection_pool — pool exhausted 14 times

Resolved (0):
  No resolved anomalies.
```

---

### Root Cause Analysis — `tinkr rca`

Runs a full AI root-cause analysis combining logs, metrics, and traces. Streams a structured report with six sections: executive summary, root cause, contributing factors, timeline, immediate actions, and prevention.

Uses `claude-opus-4-6` with extended thinking for confirmed high-severity incidents.

```bash
tinkr rca payments-api                    # last 1h
tinkr rca payments-api --since 2h         # wider window
tinkr rca payments-api --severity high    # only include high/critical anomalies
```

```
╭─ Root Cause Analysis  payments-api  window:1h ──────────────────╮

## Executive Summary
The payments-api experienced a cascading failure starting at 14:01
triggered by connection pool exhaustion on the PostgreSQL primary...

## Root Cause
db_connection_pool reached capacity (max=20) when a batch job
opened 18 long-running transactions simultaneously...

## Timeline
14:00:47  Batch job started (commit a3f2b1c — deploy 6 min earlier)
14:01:03  First connection timeout errors appear (trace a1b2c3d4)
14:01:15  Error rate crosses 10% SLO threshold
...

## Immediate Actions
1. Kill the batch job: `kubectl delete job payment-reconciler`
2. Increase pool size temporarily: set DB_POOL_MAX=40 and restart

## Prevention
- Add connection pool monitoring to tinkr alert
- Move batch jobs to a read replica
╰─────────────────────────────────────────────────────────────────╯
```

---

### Deploy tracking — `tinkr deploy`

Cross-reference recent GitHub commits with anomaly spikes. Requires GitHub configured in `config.toml`.

```bash
# List recent commits for a service
tinkr deploy list payments-api
tinkr deploy list payments-api --since 14d --limit 20

# Highlight commits that had anomalies within 30 minutes
tinkr deploy correlate payments-api
tinkr deploy correlate payments-api --since 14d
```

```
 Deploys — payments-api (7d)  |  3 anomaly(ies) in window
 SHA       Message                              Author    Time                  Nearby Anomalies
 a3f2b1c   add batch reconciliation job         alice     2026-04-07 13:54:47   • HIGH latency_p99 — 3.2s avg
                                                                                 • MEDIUM db_connection_pool
 9c8d7e6   fix null check in PaymentService     bob       2026-04-06 10:22:11   none
 3b2a1f0   upgrade stripe-sdk to 9.1.0          alice     2026-04-05 16:45:00   none
```

Commits with nearby anomalies are highlighted in red.

---

### SLO tracking — `tinkr slo`

Compute availability, error budget consumed, and burn rate from log-based error rates. A burn rate > 1× means you are consuming the error budget faster than the window allows.

```bash
tinkr slo payments-api                          # 99.9% target, 30d window
tinkr slo payments-api --target 99.5 --window 7d
tinkr slo payments-api --output json
```

```
 SLO — payments-api (window: 30d)
 Status             ✗ SLO BREACH
 Availability       99.8731%  (target: 99.9%)
 Total requests     187 432
 Error count        2 372
 Error budget used  2 372 / 187 requests
 Budget remaining   0.0%
 Burn rate          12.68×  (>1 = consuming budget faster than sustainable)
```

---

### Threshold alert rules — `tinkr alert`

Alert rules fire via your configured notifier when a metric crosses a threshold during a watch tick. Unlike watches (which trigger on any anomaly change), alert rules give you precise numeric thresholds per metric.

```bash
# Create a rule
tinkr alert create payments-api \
  --metric error_rate --op gt --threshold 5.0 \
  --severity high --notifier slack --destination "#oncall"

tinkr alert create auth-service \
  --metric latency_p99 --op gt --threshold 500 \
  --severity critical

# List all rules
tinkr alert list

# Mute during planned maintenance (30m, 2h, 1d)
tinkr alert mute alert-3a976e39 --duration 4h

# Delete permanently
tinkr alert delete alert-3a976e39
```

```
 Alert Rules
 ID             Service          Metric          Condition    Severity   Notifier   Muted Until
 alert-3a976e39 payments-api     error_rate       > 5.0        HIGH       slack      —
 alert-9b2c1d0e auth-service     latency_p99      > 500        CRITICAL   —          2026-04-07 18:00
```

**Operators:** `gt` (>)  `lt` (<)  `gte` (≥)  `lte` (≤)

---

### Background watches — `tinkr watch`

Watches run as asyncio tasks inside the server process. The server polls for anomalies on a schedule and dispatches alerts via the configured notifier when the anomaly set changes.

```bash
# Start a watch — uses the "default" notifier from the active profile
tinkr watch start payments-api
tinkr watch start payments-api --interval 120

# Route alerts to a specific notifier
tinkr watch start payments-api --notifier discord-ops
tinkr watch start payments-api --notifier slack-main --destination "#payments-oncall"

# List all watches on the server
tinkr watch list

# Stop a watch (keeps the record as 'stopped' in the DB)
tinkr watch stop watch-abc123

# Delete a watch permanently (removes the DB record entirely)
tinkr watch delete watch-abc123
```

```
 Server Watches
 ID               Service          Status    Notifier      Interval   Last Run
 watch-a3f2b1c4   payments-api     running   default        60s        2024-01-15 14:32
 watch-9e2d3b1a   auth-service     running   discord-ops   120s       2024-01-15 14:31
```

**How it works:**
1. `tinkr watch start` calls `POST /api/v1/watches` on the server
2. The server starts an asyncio task that polls `detect_anomalies` every `interval` seconds
3. A SHA-256 hash of the current anomaly set is compared to the previous tick — the notifier is only called when the set changes
4. Watch state is persisted in SQLite (`~/.tinkr/tinker.db`) and resumed on server restart
5. `tinkr watch stop` marks the record stopped; `tinkr watch delete` removes it

**Alert message format (Slack):**
```
*Tinkr Watch* — `payments-api`  [watch-a3f2b1c4]

• *HIGH* `error_count` — 847 errors in 10m (threshold: 10)
• *MEDIUM* `latency_p99` — 2.4s avg (threshold: 1s)
```

Notifiers are configured per profile — see [Profiles and notifiers](#profiles-and-notifiers) below.

---

### Other commands

```bash
# ── Stream live logs ──────────────────────────────────────────────────────────
tinkr tail payments-api
tinkr tail payments-api -q 'level:ERROR'
tinkr tail payments-api -q 'level:(ERROR OR WARN) AND "timeout"'
tinkr tail payments-api --resource ecs -q 'level:ERROR'

# ── Fetch logs (no AI) ────────────────────────────────────────────────────────
tinkr logs payments-api
tinkr logs payments-api -q 'level:ERROR' --since 30m
tinkr logs payments-api --resource lambda -q '"cold start"'

# ── Metrics ───────────────────────────────────────────────────────────────────
tinkr metrics payments-api Errors --since 2h
tinkr metrics payments-api http_requests_total --resource ecs
```

---

## Query syntax

One query syntax works on every backend. Tinkr translates it to CloudWatch Logs Insights, LogQL, GCP filter, KQL, Datadog search, or Elasticsearch DSL automatically.

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
tinkr logs payments-api --resource ecs -q 'level:ERROR'
tinkr logs my-function  --resource lambda
tinkr tail payments-api --resource eks
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

The simplest deployment is `uv tool install tinkr && tinkr-server` on any machine that has cloud access — an EC2 instance with an IAM role, a Cloud Run instance with a Workload Identity, or your laptop.

### AWS (EC2 / ECS)

```bash
# 1. Launch EC2 with an IAM role attached (see permissions below)
# 2. SSH in and:
uv tool install tinkr
tinkr-server init      # detects AWS automatically, verifies CloudWatch access
tinkr-server           # or: nohup tinkr-server start start &
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
gcloud run deploy tinkr \
  --image gcr.io/your-project/tinker-agent \
  --service-account tinkr@your-project.iam.gserviceaccount.com \
  --set-env-vars TINKR_BACKEND=gcp,GCP_PROJECT_ID=your-project
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
cp .env.example .env      # fill in TINKR_BACKEND + credentials
docker compose -f deploy/docker-compose.yml up -d
# Starts Tinkr server + Loki + Prometheus + Grafana for local testing
```

### Managing API keys

```bash
# Generate (tinkr-server init does this automatically)
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
      "headers": { "Authorization": "Bearer ${TINKR_API_TOKEN}" }
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

`tinkr-server init` asks for this interactively (Step 3). For manual setup:

```bash
# ~/.tinkr/.env
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
```

```toml
# ~/.tinkr/config.toml
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
# ~/.tinkr/config.toml

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
tinkr profile list              # show profiles + active marker
tinkr profile use aws-staging   # switch active profile (updates config.toml)
tinkr profile add               # wizard to add a new profile
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
tinkr watch start payments-api                                      # uses "default"
tinkr watch start payments-api --notifier discord-ops               # named notifier
tinkr watch start payments-api --notifier default --destination "#payments-oncall"
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

`tinkr-server init` asks for these interactively (Step 2). For manual setup:

```bash
# ~/.tinkr/.env
SLACK_BOT_TOKEN=xoxb-xxxxxxxxxxxx-xxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxxxxx
SLACK_SIGNING_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

```toml
# ~/.tinkr/config.toml
[slack]
bot_token      = "env:SLACK_BOT_TOKEN"
signing_secret = "env:SLACK_SIGNING_SECRET"
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

`tinkr-server init` writes all of this automatically.

| File | Purpose |
|---|---|
| `~/.tinkr/config.toml` | Structure — profiles, LLM, Slack, GitHub, auth, server settings |
| `~/.tinkr/.env` | Secrets — API keys, tokens. Never commit this file |

Secrets are referenced in `config.toml` as `"env:VAR_NAME"` and resolved at server startup.

### Full `~/.tinkr/config.toml` structure

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
signing_secret = "env:SLACK_SIGNING_SECRET"
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

### `~/.tinkr/.env` (secrets only)

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
| `~/.tinkr/config` | Server URL + API token — written by `tinkr init` |
| `~/.tinkr/.env` | Server secrets — written by `tinkr-server init`, auto-loaded by `tinkr-server` |
| `~/.tinkr/config.toml` | Server structure config — written by `tinkr-server init` |
| `~/.tinkr/tinker.db` | SQLite — REPL sessions, watch state, alert rules |
| `TINKR_SERVER_URL` | Override server URL (env var takes priority over `~/.tinkr/config`) |
| `TINKR_API_TOKEN` | Override API token (env var takes priority over `~/.tinkr/config`) |

### Fallback: `.env`-only mode

If `config.toml` does not exist, the server falls back to env var configuration:

| Variable | Description | Default |
|---|---|---|
| `TINKR_BACKEND` | Active backend | `cloudwatch` |
| `ANTHROPIC_API_KEY` | or `OPENROUTER_API_KEY` / `OPENAI_API_KEY` / `GROQ_API_KEY` | — |
| `TINKR_API_KEYS` | JSON array of hashed keys | `[]` |
| `TINKR_SERVER_PORT` | Bind port | `8000` |
| `TINKR_SERVER_HOST` | Bind host | `0.0.0.0` |

See [.env.example](.env.example) for the full env var reference.

---

## Security

| Concern | How Tinkr handles it |
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

# 2. Configure and start Tinkr server (separate terminal)
tinkr-server init
#   Step 1 → pick Anthropic, enter ANTHROPIC_API_KEY
#   Step 5 → pick "Self-hosted (Grafana)", enter Loki/Prometheus URLs
tinkr-server start

# 3. Point CLI at it
tinkr init    # URL: http://localhost:8000

# 4. Generate traffic and query
cd local-dev && ./generate_traffic.sh incident
tinkr anomaly payments-api --since 5m
tinkr investigate payments-api
```

---

## Development

```bash
git clone https://github.com/gettinker/tinkr && cd tinkr
uv sync                          # create .venv, install all deps

# Run via venv (no global install needed during dev)
uv run tinkr --help
uv run tinkr-server

# Install globally as editable (changes in src/ take effect immediately)
uv tool install --editable .
tinkr --help

# Tests
uv run pytest                    # all tests
uv run pytest tests/test_query/  # query translator tests
uv run ruff check src/
uv run mypy src/
```

All per-user state lives in `~/.tinkr/`:

| File | Written by | Used by |
|---|---|---|
| `~/.tinkr/config.toml` | `tinkr-server init` | `tinkr-server` (structure + routing) |
| `~/.tinkr/.env` | `tinkr-server init` | `tinkr-server` (secrets) |
| `~/.tinkr/config` | `tinkr init` | all CLI commands |
| `~/.tinkr/tinker.db` | auto-created | `tinkr investigate`, `tinkr watch`, `tinkr alert` |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to open issues and pull requests, and [DEVELOPMENT.md](DEVELOPMENT.md) for the local setup guide.

---

## License

MIT — see [LICENSE](LICENSE)

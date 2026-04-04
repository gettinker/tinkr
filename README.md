# Tinker

Open-source AI-powered observability and incident response agent. Connects to your existing cloud backend, analyzes logs and metrics, cross-references incidents with your codebase, and suggests fixes — with human approval before any code changes.

---

## Install

```bash
pip install tinker-agent
# or
uv add tinker-agent
```

Requires Python 3.11+.

---

## Quick start

### 1. Run the setup wizard

```bash
tinker init
```

```
? How do you want to use Tinker?
  ❯ Local  — run directly from this machine (dev / solo use)
    Server — connect to a deployed Tinker server (team use)
    Deploy — generate Helm / Terraform config to deploy a new server
```

Start with **Local** — no server to deploy, uses your existing cloud credentials.

---

### Local mode prerequisites

| Requirement | Notes |
|---|---|
| Cloud CLI installed | `aws` / `gcloud` / `az` — whichever matches your backend |
| Cloud credentials | `aws sso login` / `gcloud auth application-default login` / `az login` |
| Anthropic API key | or any supported LLM provider key |
| Python 3.11+ | — |

The wizard asks for your cloud and LLM key, then writes `tinker.toml` and `.env`.

```bash
# Authenticate with your cloud after running tinker init
aws sso login                           # AWS
gcloud auth application-default login   # GCP
az login                                # Azure
# Grafana / Datadog / Elastic — API keys set in .env by the wizard
```

---

### Server mode prerequisites

| Requirement | Notes |
|---|---|
| Tinker server URL | Provided by whoever deployed the server |
| Tinker API token | `TINKER_API_TOKEN` — obtained from the server operator |
| Python 3.11+ | — |

No cloud credentials needed on your machine — the server holds the IAM role.

```bash
export TINKER_API_TOKEN=<your-token>
tinker init   # pick "Server", enter the URL
```

---

### 2. Verify

```bash
tinker doctor        # confirms backend + LLM are reachable
```

```
Check    Status   Detail
──────   ──────   ──────────────────────────────────
LLM      ✓ OK     anthropic/claude-sonnet-4-6
Backend  ✓ OK     cloudwatch
```

---

## Examples

All commands take a **service name** as the first positional argument — this is the name of the service in your observability backend (e.g. the ECS service name, Cloud Run service name, Loki `service` label, etc.).

```bash
# ── Incident analysis ──────────────────────────────────────────────────────
tinker analyze payments-api                           # RCA for the last hour
tinker analyze payments-api --since 2h               # look back further
tinker analyze payments-api --since 2h -v            # stream agent reasoning
tinker analyze payments-api --deep                   # extended thinking (slower, thorough)

# ── Suggest and apply a fix ────────────────────────────────────────────────
tinker fix INC-abc123                                # show the proposed fix
tinker fix INC-abc123 --approve                      # apply fix and open a GitHub PR

# ── Stream live logs ───────────────────────────────────────────────────────
tinker tail payments-api                             # all logs, live
tinker tail payments-api -q 'level:ERROR'            # errors only
tinker tail payments-api -q 'level:(ERROR OR WARN) AND "timeout"'
tinker tail payments-api --resource ecs -q 'level:ERROR'

# ── Fetch logs (no AI) ─────────────────────────────────────────────────────
tinker logs payments-api                             # recent logs
tinker logs payments-api -q 'level:ERROR' --since 30m
tinker logs payments-api -q 'level:ERROR' --since 1h -n 200
tinker logs payments-api --resource lambda -q '"cold start"'
tinker logs payments-api --resource rds -q 'level:ERROR AND "deadlock"'

# ── Metrics ────────────────────────────────────────────────────────────────
tinker metrics payments-api Errors --since 2h
tinker metrics payments-api Latency --since 1h

# ── Background monitoring ──────────────────────────────────────────────────
tinker monitor --services payments-api,auth-service,orders-api
```

### Mode override

`tinker.toml` sets the default mode. Override it per-command with `--mode`:

```bash
tinker --mode local  logs payments-api -q 'level:ERROR'
tinker --mode server analyze payments-api
```

---

## Query syntax

One query syntax works on every backend. Tinker translates it to CloudWatch Logs Insights, LogQL, GCP filter, KQL, Datadog search, or Elasticsearch DSL automatically.

### Operators

| Pattern | Meaning |
|---|---|
| `level:ERROR` | Field match |
| `level:(ERROR OR WARN)` | Multi-value OR |
| `"connection timeout"` | Exact phrase |
| `timeout` | Substring match |
| `level:ERROR AND "timeout"` | AND (explicit) |
| `level:ERROR "timeout"` | AND (implicit) |
| `NOT "health check"` | Negation |
| `(level:ERROR OR level:WARN) AND service:payments-api` | Grouped |

Field aliases: `severity` → `level`, `svc`/`app` → `service`, `msg` → `message`, `trace` → `trace_id`

### Targeting infrastructure resources

Use `--resource TYPE` (or `-r TYPE`) to tell Tinker which infrastructure resource to query. Without it each backend auto-discovers or uses its default.

```bash
tinker logs payments-api --resource ecs -q 'level:ERROR'
tinker logs my-function --resource lambda -q '"cold start"'
tinker tail payments-api --resource eks
```

| `--resource` | CloudWatch log group | GCP resource.type | Azure KQL table | Loki label | ES index |
|---|---|---|---|---|---|
| `lambda` | `/aws/lambda/{svc}` | `cloud_function` | `FunctionAppLogs` | `resource="lambda"` | `lambda-*` |
| `ecs` | `/ecs/{svc}` | `cloud_run_revision` | `ContainerLog` | `resource="container"` | `ecs-*` |
| `eks` / `k8s` | `/aws/containerinsights/{svc}/application` | `k8s_container` | `ContainerLog` | `resource="container"` | `kubernetes-*` |
| `ec2` / `vm` / `host` | `/aws/ec2/{svc}` | `gce_instance` | `Syslog` | `resource="host"` | `syslog-*` |
| `rds` / `db` | `/aws/rds/instance/{svc}/postgresql` | `cloudsql_database` | `AzureDiagnostics` | `resource="db"` | `rds-*` |
| `apigw` | `API-Gateway-Execution-Logs_{svc}/prod` | — | `ApiManagementGatewayLogs` | `resource="apigw"` | `apigw-*` |
| `cloudrun` | `/ecs/{svc}` | `cloud_run_revision` | `ContainerLog` | `resource="container"` | `ecs-*` |
| `gke` / `aks` | `/aws/containerinsights/{svc}/application` | `k8s_container` | `ContainerLog` | `resource="container"` | `kubernetes-*` |
| `appservice` | — | — | `AppServiceConsoleLogs` | `resource="container"` | `appservice-*` |
| (none) | auto-discover | `cloud_run_revision` | `AppTraces` | — | `logs-*` |

Cross-cloud aliases work — `--resource lambda` on GCP maps to `cloud_function`, `--resource ecs` on Azure maps to `ContainerLog`. You never change flags when switching backends.

### How queries map to native syntax

| Tinker | CloudWatch Insights | LogQL | GCP filter | KQL |
|---|---|---|---|---|
| `level:ERROR` | `level = 'ERROR'` | `{level="ERROR"}` | `severity="ERROR"` | `SeverityLevel == "Error"` |
| `"timeout"` | `@message like /timeout/` | `\|= \`timeout\`` | `textPayload:"timeout"` | `Message contains "timeout"` |
| `--resource ecs` | log group `/ecs/{svc}` | `{resource="container"}` | `resource.type="cloud_run_revision"` | table `ContainerLog` |

Raw native queries (LogQL `{...}`, Insights `| filter ...`, KQL `| where ...`) are accepted unchanged.

---

## Supported backends

| Backend | Logs | Metrics | Traces | Auth |
|---|---|---|---|---|
| `cloudwatch` | CloudWatch Logs Insights | CloudWatch Metrics | X-Ray | IAM Task Role |
| `gcp` | Cloud Logging | Cloud Monitoring | Cloud Trace | Workload Identity |
| `azure` | Log Analytics / KQL | Azure Monitor Metrics | App Insights | Managed Identity |
| `grafana` | Loki / LogQL | Prometheus / PromQL | Tempo | API key |
| `datadog` | Logs API v2 | Metrics API v1 | APM Traces | API key + App key |
| `elastic` | Elasticsearch / OpenSearch | Aggregations | APM | API key |

Set `TINKER_BACKEND` to select the active backend.

---

## Supported LLM providers

Uses [LiteLLM](https://github.com/BerriAI/litellm) — swap providers by changing one env var.

| Provider | `TINKER_DEFAULT_MODEL` | Key variable |
|---|---|---|
| Anthropic | `anthropic/claude-sonnet-4-6` | `ANTHROPIC_API_KEY` |
| OpenRouter | `openrouter/anthropic/claude-opus-4-6` | `OPENROUTER_API_KEY` |
| OpenAI | `openai/gpt-4o` | `OPENAI_API_KEY` |
| Groq | `groq/llama-3.1-70b-versatile` | `GROQ_API_KEY` |
| Ollama (local) | `ollama/llama3` | — |

---

## Live log streaming — `tinker tail`

Streams new entries as they arrive. Uses native streaming where the backend supports it, falls back to polling otherwise.

| Backend | Mechanism | Latency |
|---|---|---|
| `grafana` / Loki | Websocket (`/loki/api/v1/tail`) | Real-time |
| `cloudwatch` | Poll every N seconds | ≈ poll interval |
| `gcp` | Poll every N seconds | ≈ poll interval |
| `azure` | Poll every N seconds | ≈ poll interval |
| `datadog` | Poll every N seconds | ≈ poll interval |
| `elastic` | Poll every N seconds | ≈ poll interval |

```bash
tinker tail <service>                              # all logs
tinker tail <service> -q 'level:ERROR'             # filtered
tinker tail <service> --poll 5                     # poll every 5s
```

---

## Deploy a Tinker server

**Local mode** needs no server. The rest of this section is for teams who want shared access, Slack alerts, the monitoring loop, and the Claude Code MCP integration.

The server is deployed with your team's existing infra tooling — Helm, Terraform, or Docker Compose. Run `tinker init` → Deploy to generate the config files, then deploy them through your normal process.

### Prerequisites

| Requirement | Helm | Terraform | Docker Compose |
|---|---|---|---|
| `kubectl` + cluster access | ✓ | — | — |
| `helm` v3+ | ✓ | — | — |
| `terraform` v1.5+ | — | ✓ | — |
| `docker` + `docker compose` | — | — | ✓ |
| Cloud CLI (`aws`/`gcloud`/`az`) | for secrets setup | ✓ | for secrets setup |
| Admin cloud credentials | for IAM/secrets setup | ✓ | for secrets setup |

### Helm (EKS / GKE / AKS)

```bash
tinker init          # pick "Deploy" → "Helm on EKS/GKE/AKS"
                     # → writes tinker-values.yaml

# Store secrets (wizard prints exact commands per cloud), then:
kubectl create secret generic tinker-secrets \
  --from-literal=anthropic-api-key=sk-ant-... \
  --from-literal=api-keys='[{"hash":"...","subject":"default","roles":["sre"]}]'

helm install tinker ./deploy/helm/tinker -f tinker-values.yaml

kubectl get svc tinker   # get the URL, add to tinker.toml [server] url
```

For IRSA (EKS), Workload Identity (GKE), or Azure Workload Identity (AKS) — add the annotation to `serviceAccount.annotations` in your values file. Examples are in [deploy/helm/tinker/values.yaml](deploy/helm/tinker/values.yaml).

### Terraform (ECS Fargate / Cloud Run / Container Apps)

```bash
tinker init          # pick "Deploy" → "Terraform — ..."
                     # → writes tinker.tfvars

cd deploy/terraform/aws     # or gcp / azure
terraform init && terraform apply -var-file=../../../tinker.tfvars

terraform output service_url   # add to tinker.toml [server] url
```

Modules are in [`deploy/terraform/`](deploy/terraform/). Each creates the compute resource, IAM role, secrets manager wiring, and role assignments.

### Docker Compose (self-hosted)

```bash
tinker init          # pick "Deploy" → "Docker Compose"
                     # → writes tinker-server.env

cp tinker-server.env deploy/.env
docker compose -f deploy/docker-compose.yml up -d
```

### Secrets

| Cloud | Service | Keys |
|---|---|---|
| AWS | Secrets Manager | `tinker/anthropic-api-key`, `tinker/api-keys` |
| GCP | Secret Manager | `tinker-anthropic-api-key`, `tinker-api-keys` |
| Azure | Key Vault | `anthropic-api-key`, `tinker-api-keys` |
| Self-hosted | `.env` (not committed) | plain env vars |

---

## Claude Code (remote MCP)

Once the server is deployed, add Tinker as a remote MCP server in `.claude/settings.json`:

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

Invite `@tinker` to any channel:

```
/tinker-analyze <service> since=2h
/tinker-fix INC-abc123
/tinker-approve INC-abc123          (requires oncall role)
/tinker-status
```

The bot posts proactive alerts when the monitoring loop detects anomalies. Alerts include inline buttons: **Get Fix** / **Approve** / **Dismiss**.

---

## How it works

```
┌──────────────────────────────────────────────────────────────────┐
│  Tinker Server  (deployed once — optional for team use)          │
│                                                                  │
│  POST /api/v1/analyze  ──► SSE streaming RCA                     │
│  GET  /mcp/sse         ──► Remote MCP for Claude Code            │
│  POST /slack/events    ──► Slack bot                             │
│                                                                  │
│  TINKER_BACKEND=cloudwatch|gcp|azure|grafana|datadog|elastic     │
│  Credentials → cloud-native identity. Zero long-lived keys.      │
└───────────────────────┬──────────────────────────────────────────┘
                        │  API key
          ┌─────────────┼──────────────────┐
          ▼             ▼                  ▼
       CLI           Claude Code        Slack Bot
      (thin)         remote MCP         (webhook)
```

**Local mode** — CLI talks directly to the cloud using your laptop credentials. No server needed.

**Server mode** — CLI and Slack bot authenticate to the server with a short API key. The server holds the IAM role / Managed Identity — your laptop never needs cloud credentials.

---

## Configuration

`tinker init` writes all of this for you. For manual configuration:

### Core

| Variable | Description |
|---|---|
| `TINKER_BACKEND` | Active backend: `cloudwatch` `gcp` `azure` `grafana` `datadog` `elastic` |
| `ANTHROPIC_API_KEY` | or `OPENROUTER_API_KEY` / `OPENAI_API_KEY` / `GROQ_API_KEY` |
| `TINKER_DEFAULT_MODEL` | e.g. `anthropic/claude-sonnet-4-6` |
| `TINKER_DEEP_RCA_MODEL` | Model for `--deep` analysis, e.g. `anthropic/claude-opus-4-6` |
| `TINKER_API_KEYS` | JSON array of hashed keys — server mode only |
| `TINKER_SERVER_PORT` | Default `8000` |

### Per-backend

| Backend | Variables |
|---|---|
| `cloudwatch` | `AWS_REGION` — credentials from IAM role |
| `gcp` | `GCP_PROJECT_ID` — credentials from Workload Identity |
| `azure` | `AZURE_LOG_ANALYTICS_WORKSPACE_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP` |
| `grafana` | `GRAFANA_LOKI_URL`, `GRAFANA_PROMETHEUS_URL`, `GRAFANA_TEMPO_URL` |
| `datadog` | `DATADOG_API_KEY`, `DATADOG_APP_KEY`, `DATADOG_SITE` |
| `elastic` | `ELASTICSEARCH_URL`, `ELASTICSEARCH_API_KEY` |

See [.env.example](.env.example) for the full reference.

### Managing API keys (server mode)

```bash
# Generate a new key
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Hash it (store the hash on the server, give the raw key to the client)
python -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" <raw-key>

# Add to TINKER_API_KEYS on the server
TINKER_API_KEYS='[{"hash":"<sha256>","subject":"alice","roles":["sre"]}]'
```

---

## Security

| Concern | How Tinker handles it |
|---|---|
| Cloud credentials | Never stored — server uses IAM role / Workload Identity / Managed Identity |
| Client auth | API keys (SHA-256 hashed at rest) |
| Destructive operations | `apply_fix` and `create_pr` require explicit `/approve` — blocked by default in Claude Code |
| RBAC | Slack commands gated by user group → role mapping |
| Prompt injection | Log content sanitized before inclusion in any LLM prompt |
| Fix safety | Proposed diffs scanned with Semgrep before being shown to the user |
| Secrets in logs | Credentials stripped from all log data before LLM submission |

---

## Local development

The [`local-dev/`](local-dev/) directory runs a complete observability stack locally — no cloud account needed.

### Stack

| Service | Port | Purpose |
|---|---|---|
| `payments-api` | 7001 | Dummy microservice — emits structured logs at all levels + Prometheus metrics |
| `loki` | 3100 | Log storage |
| `prometheus` | 9090 | Metrics |
| `grafana` | 3000 | Dashboards |

The Tinker server is **not** in this compose — run it from your IDE for hot reload and breakpoints.

### Setup

```bash
# 1. Start the stack
cd local-dev && ./run.sh

# 2. Start the Tinker server (separate terminal)
cp .env.example .env
# Set: TINKER_BACKEND=grafana, GRAFANA_LOKI_URL=http://localhost:3100, ANTHROPIC_API_KEY=...
uv run tinker-server

# 3. Generate traffic
./generate_traffic.sh           # steady mixed traffic (Ctrl-C to stop)
./generate_traffic.sh incident  # simulate an error spike
./generate_traffic.sh burst     # 100 rapid requests

# 4. Query
tinker tail payments-api -q 'level:ERROR'
tinker analyze payments-api --since 5m -v
tinker logs payments-api -q 'level:ERROR AND "timeout"'
```

### Dummy service endpoints

| Endpoint | Emits |
|---|---|
| `GET /pay` | Random weighted scenario |
| `GET /pay/ok` | INFO — successful payment |
| `GET /pay/error` | ERROR — payment failed |
| `GET /pay/slow` | WARN — slow database query |
| `GET /pay/critical` | CRITICAL — circuit breaker open |
| `GET /metrics` | Prometheus metrics |
| `GET /health` | Health check |

```bash
cd local-dev && ./run.sh down   # tear down
```

---

## Development

```bash
uv sync
uv run pytest                    # all tests
uv run pytest tests/test_query/  # query translator tests
uv run ruff check src/           # lint
uv run mypy src/                 # type check
```

---

## License

MIT

# Tinker

Open-source AI-powered observability and incident response agent. Tinker runs in your cloud, monitors your infrastructure, cross-references incidents with your codebase, and suggests fixes — with human approval before any code changes.

Works with every major cloud provider and observability stack out of the box.

---

## How it works

```
┌──────────────────────────────────────────────────────────────────┐
│  Tinker Server  (deploy once in your cloud account)              │
│                                                                  │
│  POST /api/v1/analyze  ──► SSE streaming RCA                     │
│  GET  /mcp/sse         ──► Remote MCP for Claude Code / editors  │
│  POST /slack/events    ──► Slack bot                             │
│  GET  /health                                                    │
│                                                                  │
│  Active backend (one env var):                                   │
│  cloudwatch | gcp | azure | grafana | datadog | elastic          │
│                                                                  │
│  Credentials → cloud-native identity. Zero long-lived keys.      │
└───────────────────────┬──────────────────────────────────────────┘
                        │  API key
          ┌─────────────┼──────────────────┐
          ▼             ▼                  ▼
       CLI           Claude Code        Slack Bot
      (thin)         remote MCP         (webhook)
                     over SSE
```

1. **Tinker Server** runs in your cloud with a read-only IAM role / Managed Identity — no credentials in code or containers
2. **CLI and Slack bot** are thin clients authenticated to the server via a short API key
3. **Claude Code** connects via the `/mcp/sse` endpoint as a remote MCP server
4. Set `TINKER_BACKEND` to point at your observability stack — the rest is automatic

---

## Supported backends

| Backend | Logs | Metrics | Traces | Auth (no long-lived keys) |
|---|---|---|---|---|
| `cloudwatch` | CloudWatch Logs Insights | CloudWatch Metrics | X-Ray | ECS Task Role / Lambda Execution Role |
| `gcp` | Cloud Logging | Cloud Monitoring | Cloud Trace | Workload Identity (Cloud Run SA) |
| `azure` | Log Analytics / KQL | Azure Monitor Metrics | Application Insights | Managed Identity |
| `grafana` | Loki / LogQL | Prometheus / PromQL | Tempo | API key or basic auth |
| `datadog` | Logs API v2 | Metrics API v1 | APM Traces | API key + App key |
| `elastic` | Elasticsearch / OpenSearch | Aggregations | APM | API key |

---

## Quick start

### Option A — Local dev (no cloud account needed)

Spins up Tinker + Loki + Prometheus + Grafana in Docker:

```bash
git clone https://github.com/your-org/tinker.git
cd tinker
cp .env.example .env
# Set ANTHROPIC_API_KEY in .env — that's the only required variable
docker compose -f deploy/docker-compose.yml up
```

Tinker is now running at `http://localhost:8000`.

### Option B — Against a real cloud backend

```bash
uv sync
cp .env.example .env
# Set ANTHROPIC_API_KEY + TINKER_BACKEND + backend-specific vars
uv run tinker-server
```

---

## CLI

The CLI talks to a running Tinker server. Point it at your deployment:

```bash
export TINKER_SERVER_URL=http://localhost:8000   # or your deployed URL
export TINKER_API_TOKEN=<your-api-key>

# Analyze a service for incidents in the last hour
tinker analyze payments-api --since 1h

# Stream agent reasoning step by step
tinker analyze payments-api --since 2h --verbose

# Use extended thinking for deep root cause analysis
tinker analyze payments-api --since 6h --deep

# Get and apply a fix (--approve required)
tinker fix INC-abc123
tinker fix INC-abc123 --approve

# Tail raw logs (no AI)
tinker logs payments-api --query "level:ERROR" --since 30m

# Start the monitoring loop (prints anomalies to stdout)
tinker monitor --services payments-api,auth-service
```

---

## Slack bot

Invite `@tinker` to any channel:

```
/tinker-analyze payments-api since=2h
/tinker-fix INC-abc123
/tinker-approve INC-abc123          (requires oncall role)
/tinker-status
/tinker-help
```

The bot posts proactive alerts when the monitoring loop detects anomalies.
Alerts include inline buttons: **Get Fix** / **Approve** / **Dismiss**.

---

## Claude Code (remote MCP)

Once deployed, add Tinker as a remote MCP server in `.claude/settings.json`:

```json
{
  "mcpServers": {
    "tinker": {
      "transport": "sse",
      "url": "https://tinker.your-company.internal/mcp/sse",
      "headers": {
        "Authorization": "Bearer ${TINKER_API_TOKEN}"
      }
    }
  }
}
```

Claude can then call `query_logs`, `get_metrics`, `detect_anomalies`, `search_code`, and `suggest_fix` directly from your editor.

---

## Deployment

### AWS (ECS Fargate)

```bash
# 1. Create the IAM role with the minimal read-only policy
aws iam create-role --role-name tinker-readonly ...
aws iam put-role-policy --role-name tinker-readonly \
  --policy-document file://deploy/aws/iam-policy.json

# 2. Store secrets
aws secretsmanager create-secret --name tinker/anthropic-api-key \
  --secret-string "sk-ant-..."

# 3. Push image and register task
aws ecr create-repository --repository-name tinker
docker build -f deploy/Dockerfile -t tinker .
docker push <ecr-url>/tinker:latest
aws ecs register-task-definition \
  --cli-input-json file://deploy/aws/task-definition.json
```

The task definition in [deploy/aws/task-definition.json](deploy/aws/task-definition.json) wires the IAM role and pulls all secrets from Secrets Manager — **no credentials in the container**.

### GCP (Cloud Run)

```bash
# 1. Create a service account with read-only roles
gcloud iam service-accounts create tinker-readonly
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:tinker-readonly@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/logging.viewer"
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:tinker-readonly@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/monitoring.viewer"

# 2. Store secrets in Secret Manager
echo -n "sk-ant-..." | gcloud secrets create tinker-anthropic-api-key --data-file=-

# 3. Deploy
gcloud run services replace deploy/gcp/cloudrun.yaml
```

The service account is attached via Workload Identity — **no key file anywhere**.

### Azure (Container Apps)

```bash
# 1. Enable system-assigned managed identity (done in the manifest)
# 2. Assign roles
az role assignment create --assignee <managed-identity-principal-id> \
  --role "Monitoring Reader" --scope /subscriptions/SUBSCRIPTION_ID

# 3. Store secrets in Key Vault
az keyvault secret set --vault-name tinker-vault \
  --name anthropic-api-key --value "sk-ant-..."

# 4. Deploy
az containerapp create --yaml deploy/azure/container-app.yaml
```

See [deploy/azure/container-app.yaml](deploy/azure/container-app.yaml) for the full manifest.

---

## Configuration

Set `TINKER_BACKEND` and the variables for that backend. Everything else is optional.

### Core (all deployments)

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key **(required)** |
| `TINKER_BACKEND` | Active backend: `cloudwatch` `gcp` `azure` `grafana` `datadog` `elastic` |
| `TINKER_API_KEYS` | JSON array of hashed API keys for client auth (see below) |
| `TINKER_SERVER_PORT` | Default `8000` |

### Per-backend

| Backend | Variables |
|---|---|
| `cloudwatch` | `AWS_REGION` — credentials from IAM role (no keys needed) |
| `gcp` | `GCP_PROJECT_ID` — credentials from Workload Identity (no keys needed) |
| `azure` | `AZURE_LOG_ANALYTICS_WORKSPACE_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP` — credentials from Managed Identity |
| `grafana` | `GRAFANA_LOKI_URL`, `GRAFANA_PROMETHEUS_URL`, `GRAFANA_TEMPO_URL`, optionally `GRAFANA_API_KEY` |
| `datadog` | `DATADOG_API_KEY`, `DATADOG_APP_KEY`, `DATADOG_SITE` |
| `elastic` | `ELASTICSEARCH_URL`, `ELASTICSEARCH_API_KEY` |

See [.env.example](.env.example) for the complete reference.

### Generating API keys for clients

```bash
# 1. Generate a raw key
python -c "import secrets; print(secrets.token_urlsafe(32))"

# 2. Hash it (store the hash in TINKER_API_KEYS, give the raw key to the client)
python -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" <raw-key>

# 3. Set TINKER_API_KEYS on the server
TINKER_API_KEYS='[{"hash":"<sha256>","subject":"cli-alice","roles":["sre"]}]'
```

---

## Security

| Concern | How Tinker handles it |
|---|---|
| Cloud credentials | Never stored — server uses IAM role / Workload Identity / Managed Identity |
| Client auth | Short API keys (SHA-256 hashed at rest) or short-lived JWTs via your IdP |
| Destructive operations | `apply_fix` and `create_pr` require explicit `/approve` — blocked by default in Claude Code |
| RBAC | Slack commands gated by user group → role mapping |
| Prompt injection | Log content sanitized with regex before being included in any LLM prompt |
| Fix safety | Proposed diffs scanned with Semgrep before being shown to the user |
| Audit trail | Every agent tool call logged with actor, session ID, timestamp, and approval chain |
| Secrets in logs | Credentials stripped from all log data before storage or LLM submission |

---

## Development

```bash
uv sync
uv run pytest             # all tests
uv run pytest -k backend  # backend tests only
uv run ruff check src/    # lint
uv run mypy src/          # type check
```

See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the phased roadmap.

---

## License

MIT

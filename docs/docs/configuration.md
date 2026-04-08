---
sidebar_position: 8
title: Configuration Reference
---

# Configuration Reference

Tinkr uses two configuration files, both located in `~/.tinkr/`:

| File | Purpose |
|---|---|
| `~/.tinkr/config.toml` | Profiles, backends, notifiers, auth |
| `~/.tinkr/.env` | Secrets (API keys, tokens, URLs) |

The `.env` file is never committed to source control. Values referenced as `"env:VAR_NAME"` in `config.toml` are substituted at load time.

---

## Full config.toml reference

```toml
# ──────────────────────────────────────────────
# Server connection (CLI side)
# ──────────────────────────────────────────────
[server]
url   = "http://localhost:8000"   # Tinkr server URL
token = "env:TINKR_API_TOKEN"    # Raw API token (not the hash)


# ──────────────────────────────────────────────
# Authentication (server side)
# ──────────────────────────────────────────────
[auth]
# List of hashed API keys. Generate:
#   python -c "import secrets; print(secrets.token_urlsafe(32))"
# Hash:
#   python -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" <raw>
api_keys = [
  { hash = "<sha256>", subject = "alice", roles = ["oncall"] },
  { hash = "<sha256>", subject = "bob",   roles = ["viewer"] },
]


# ──────────────────────────────────────────────
# GitHub integration (optional)
# ──────────────────────────────────────────────
[github]
token        = "env:GITHUB_TOKEN"    # Fine-grained PAT
default_repo = "acme/monorepo"       # Fallback if service has no repo


# ──────────────────────────────────────────────
# Slack integration (optional)
# ──────────────────────────────────────────────
[slack]
bot_token      = "env:SLACK_BOT_TOKEN"
signing_secret = "env:SLACK_SIGNING_SECRET"
alerts_channel = "#incidents"


# ──────────────────────────────────────────────
# Profiles
# ──────────────────────────────────────────────

# Grafana (default — local dev)
[profiles.default]
backend        = "grafana"
loki_url       = "env:GRAFANA_LOKI_URL"
prometheus_url = "env:GRAFANA_PROMETHEUS_URL"
tempo_url      = "env:GRAFANA_TEMPO_URL"

# AWS CloudWatch
[profiles.aws-prod]
backend          = "cloudwatch"
region           = "us-east-1"
log_group_prefix = "/ecs/"

[profiles.aws-prod.services.payments-api]
repo          = "acme/payments"
resource_type = "ecs"

[profiles.aws-prod.services.auth-service]
repo          = "acme/auth"
resource_type = "ecs"

[profiles.aws-prod.notifiers.pagerduty]
type                 = "webhook"
url                  = "env:PAGERDUTY_WEBHOOK_URL"
header_Authorization = "env:PAGERDUTY_API_KEY"

[profiles.aws-prod.notifiers.slack-oncall]
type      = "slack"
bot_token = "env:SLACK_BOT_TOKEN"
channel   = "#prod-incidents"

# GCP
[profiles.gcp-staging]
backend    = "gcp"
project_id = "acme-staging-123456"

# Azure
[profiles.azure-prod]
backend         = "azure"
workspace_id    = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
subscription_id = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
resource_group  = "prod-rg"

# Datadog
[profiles.datadog-prod]
backend = "datadog"
site    = "datadoghq.com"

# Elastic
[profiles.elastic-prod]
backend       = "elastic"
url           = "env:ELASTIC_URL"
index_pattern = "logs-*,filebeat-*"

# OTel
[profiles.otel-prod]
backend        = "otel"
opensearch_url = "env:OTEL_OPENSEARCH_URL"
prometheus_url = "env:OTEL_PROMETHEUS_URL"
```

---

## Full .env reference

```bash title="~/.tinkr/.env"
# ── LLM ──────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-...

# ── Server ───────────────────────────────────
TINKR_SERVER_URL=https://tinker.acme.internal
TINKR_API_TOKEN=<raw-token>        # CLI uses this
TINKR_API_KEYS='[{"hash":"<sha256>","subject":"alice","roles":["oncall"]}]'

# ── Backend: Grafana ─────────────────────────
GRAFANA_LOKI_URL=http://loki:3100
GRAFANA_PROMETHEUS_URL=http://prometheus:9090
GRAFANA_TEMPO_URL=http://tempo:3200

# ── Backend: CloudWatch ──────────────────────
AWS_REGION=us-east-1
# No access keys needed — use IAM role in production

# ── Backend: GCP ─────────────────────────────
GCP_PROJECT_ID=acme-prod-123456
# No service account key — use Workload Identity in production

# ── Backend: Azure ───────────────────────────
AZURE_LOG_ANALYTICS_WORKSPACE_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
AZURE_SUBSCRIPTION_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
AZURE_RESOURCE_GROUP=prod-rg
AZURE_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx   # Managed Identity

# ── Backend: Datadog ─────────────────────────
DD_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
DD_APP_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
DD_SITE=datadoghq.com

# ── Backend: Elastic ─────────────────────────
ELASTIC_URL=https://elastic.acme.internal:9200
ELASTIC_API_KEY=VnVhQ2ZHY0JDZGJrZXctATxxxxxxxxxxxxxxxx==

# ── Backend: OTel ────────────────────────────
OTEL_OPENSEARCH_URL=http://opensearch:9200
OTEL_PROMETHEUS_URL=http://prometheus:9090

# ── GitHub ───────────────────────────────────
GITHUB_TOKEN=github_pat_xxxxxxxxxxxxxxxx

# ── Slack ────────────────────────────────────
SLACK_BOT_TOKEN=xoxb-xxxxxxxxxxxx-xxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxxxxx
SLACK_SIGNING_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# ── Notifiers ────────────────────────────────
PAGERDUTY_WEBHOOK_URL=https://events.pagerduty.com/integration/XXXX/enqueue
PAGERDUTY_API_KEY=Token token=XXXX
OPSGENIE_WEBHOOK_URL=https://api.opsgenie.com/v2/alerts
OPSGENIE_API_KEY=GenieKey XXXX
DISCORD_OPS_WEBHOOK_URL=https://discord.com/api/webhooks/1234567890/xxxx
```

---

## Environment variable precedence

For server-side configuration, Tinkr reads variables in this order:

1. Process environment (injected by cloud secrets manager at container start)
2. `.env` file at `~/.tinkr/.env`
3. `config.toml` `env:` references (resolved from #1 and #2)

---

## Roles

| Role | Allowed actions |
|---|---|
| `viewer` | Read-only: logs, metrics, anomaly, trace, diff, slo, deploy, rca |
| `oncall` | All viewer actions + `fix`, `approve`, `watch`, `alert` |
| `sre-lead` | All oncall actions (same permissions, different subject label) |

---

## Profile active resolution

The active profile is resolved in this order:

1. `--profile` flag on the command
2. `TINKR_PROFILE` environment variable
3. Profile with `active = true` in `config.toml`
4. Profile named `default`

---

## Setup wizards

The interactive wizards generate `config.toml` and `.env` from prompts:

```bash
# Set up the server (run on the machine with cloud access)
tinkr-server init

# Connect the CLI to a running server (run on developer machines)
tinkr init
```

`tinkr-server init` asks for:
1. LLM provider and API key
2. Slack tokens (optional)
3. GitHub token (optional)
4. Server API key (for CLI auth)
5. Cloud backend and profile

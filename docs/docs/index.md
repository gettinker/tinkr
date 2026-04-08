---
slug: /
sidebar_position: 1
title: Quick Start
---

# Tinkr — Quick Start

**Tinkr** is an open-source, AI-powered observability and incident response agent. It connects to your existing cloud observability stack, detects anomalies, traces them back to root causes in your source code, and proposes fixes — with human approval before any code changes are made.

:::info GitHub Repository
[https://github.com/gettinker/tinkr](https://github.com/gettinker/tinkr)
:::

---

## How it works

```
┌─────────────────────────────────────────────────────────────────┐
│  Tinkr Server  (runs anywhere with cloud access)                │
│                                                                 │
│  POST /api/v1/rca        full AI root-cause analysis (SSE)      │
│  POST /api/v1/anomalies  anomaly detection                      │
│  POST /api/v1/traces     distributed trace fetch                │
│  POST /api/v1/slo        SLO / error budget computation         │
│  POST /api/v1/watches    background anomaly watches             │
│  POST /api/v1/alerts     threshold-based alert rules            │
│  GET  /api/v1/deploys    GitHub deploy history                  │
│  GET  /mcp/sse           Remote MCP for Claude Code             │
│  POST /slack/events      Slack bot                              │
│                                                                 │
│  Credentials → IAM role / Workload Identity / Managed Identity  │
│  Zero long-lived cloud keys on the server.                      │
└─────────────────────────────┬───────────────────────────────────┘
                              │  API key  (TINKR_API_TOKEN)
                 ┌────────────┼──────────────────┐
                 ▼            ▼                  ▼
                CLI          Claude Code        Slack Bot
               (thin)        Remote MCP         (webhook → server)
```

The server is the single point of credential trust. Cloud credentials (IAM role, Workload Identity, Managed Identity) stay on the server. The `tinkr` CLI and Slack bot authenticate with a short API token — they never touch cloud credentials directly.

---

## 5-minute setup

### Step 1 — Install

```bash
# uv (recommended)
uv tool install tinkr

# pipx
pipx install tinkr

# pip
pip install tinkr
```

See [Installation](/install) for all options including building from source.

### Step 2 — Pick a backend

Tinkr connects to your existing observability stack. Before running the wizard, decide which backend matches your setup:

| If you use… | Set `TINKR_BACKEND` to… |
|---|---|
| AWS CloudWatch | `cloudwatch` |
| Google Cloud Logging/Monitoring | `gcp` |
| Azure Monitor | `azure` |
| Grafana / Loki / Prometheus | `grafana` |
| Datadog | `datadog` |
| Elasticsearch | `elastic` |
| OpenTelemetry / OpenSearch | `otel` |

See [Backends](/backends) for credential requirements and config details for each option.

### Step 3 — Set up the server

On the machine that has cloud access (EC2, Cloud Run, your laptop):

```bash
tinkr-server init
```

The wizard walks you through:

1. **LLM provider** — Anthropic, OpenRouter, OpenAI, or Groq
2. **Slack bot** — optional, for `/tinkr-*` slash commands
3. **GitHub** — for code investigation and auto-PRs
4. **Server API key** — for CLI authentication
5. **Profiles** — your cloud backend, services, and notifiers

It writes `~/.tinkr/config.toml` and `~/.tinkr/.env`.

### Step 4 — Start the server

```bash
tinkr-server start
# Listening on http://0.0.0.0:8000
```

### Step 5 — Connect the CLI

```bash
tinkr init
# Tinkr server URL [http://localhost:8000]: https://tinkr.acme.internal
# API token: <paste your token>
# ✓ Connected: Tinkr v0.1.0b1  backend=cloudwatch
```

### Step 6 — Run your first query

```bash
tinkr doctor                          # check server + backend
tinkr anomaly payments-api            # detect anomalies (last 1h)
tinkr investigate payments-api        # interactive REPL
tinkr rca payments-api                # full AI root-cause analysis
```

---

## Core incident response workflow

```bash
# 1. Something looks wrong — check for anomalies
tinkr anomaly payments-api --since 30m

# 2. Compare to an hour ago — is it getting worse?
tinkr diff payments-api --baseline 2h --compare 1h

# 3. Did a recent deploy cause it?
tinkr deploy correlate payments-api --since 7d

# 4. Check SLO health
tinkr slo payments-api --target 99.9

# 5. Run full AI root-cause analysis
tinkr rca payments-api --since 1h

# 6. Drill into error patterns interactively, get a fix, open a PR
tinkr investigate payments-api
```

---

## What's next

- [Installation](/install) — all install options and system requirements
- [Deployment](/deployment/aws) — run the server on AWS, GCP, Azure, or Docker
- [Commands](/commands) — full CLI reference
- [Backends](/backends) — CloudWatch, GCP, Azure, Grafana, Datadog, Elastic, OTel
- [GitHub integration](/integrations/github) — code investigation and auto-PRs
- [Slack bot](/integrations/slack) — slash commands for your team

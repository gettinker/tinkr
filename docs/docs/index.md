---
slug: /
sidebar_position: 1
title: Quick Start
---

# Tinker — Quick Start

**Tinker** is an open-source, AI-powered observability and incident response agent. It connects to your existing cloud observability stack, detects anomalies, traces them back to root causes in your source code, and proposes fixes — with human approval before any code changes are made.

:::info GitHub Repository
[https://github.com/gettinker/tinkr](https://github.com/gettinker/tinkr)
:::

---

## How it works

```
┌─────────────────────────────────────────────────────────────────┐
│  Tinker Server  (runs anywhere with cloud access)               │
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
                              │  API key  (TINKER_API_TOKEN)
                 ┌────────────┼──────────────────┐
                 ▼            ▼                  ▼
                CLI          Claude Code        Slack Bot
               (thin)        Remote MCP         (webhook → server)
```

The server is the single point of credential trust. Cloud credentials (IAM role, Workload Identity, Managed Identity) stay on the server. The CLI and Slack bot authenticate with a short API token — they never touch cloud credentials directly.

---

## 5-minute setup

### Step 1 — Install Tinker

```bash
pip install tinker-agent
```

Requires Python 3.12+. See [Installation](/install) for full options.

### Step 2 — Run the server setup wizard

On the machine that has cloud access (EC2, Cloud Run, your laptop):

```bash
tinker init server
```

The wizard walks you through:

1. **LLM provider** — Anthropic, OpenRouter, OpenAI, or Groq
2. **Slack bot** — optional, for `/tinker-*` slash commands
3. **GitHub** — for code investigation and auto-PRs
4. **Server API key** — for CLI authentication
5. **Profiles** — your cloud backend, services, and notifiers

It writes `~/.tinker/config.toml` and `~/.tinker/.env`.

### Step 3 — Start the server

```bash
tinker server
# Listening on http://0.0.0.0:8000
```

### Step 4 — Connect the CLI on your machine

```bash
tinker init cli
# Tinker server URL [http://localhost:8000]: https://tinker.acme.internal
# API token: <paste your token>
# ✓ Connected: Tinker v0.1.0  backend=cloudwatch
```

### Step 5 — Verify and run your first query

```bash
tinker doctor                          # check server + backend
tinker anomaly payments-api            # detect anomalies (last 1h)
tinker investigate payments-api        # interactive REPL
tinker rca payments-api                # full AI root-cause analysis
```

---

## Core incident response workflow

```bash
# 1. Something looks wrong — check for anomalies
tinker anomaly payments-api --since 30m

# 2. Compare to an hour ago — is it getting worse?
tinker diff payments-api --baseline 2h --compare 1h

# 3. Did a recent deploy cause it?
tinker deploy correlate payments-api --since 7d

# 4. Check SLO health
tinker slo payments-api --target 99.9

# 5. Run full AI root-cause analysis
tinker rca payments-api --since 1h

# 6. Drill into error patterns interactively, get a fix, open a PR
tinker investigate payments-api
```

---

## What's next

- [Installation](/install) — all install options and system requirements
- [Deployment](/deployment/aws) — run the server on AWS, GCP, Azure, or Docker
- [Commands](/commands) — full CLI reference
- [Backends](/backends) — CloudWatch, GCP, Azure, Grafana, Datadog, Elastic, OTel
- [GitHub integration](/integrations/github) — code investigation and auto-PRs
- [Slack bot](/integrations/slack) — slash commands for your team

# Tinker

AI-powered observability and incident response agent. Tinker monitors your infrastructure, analyzes issues against your codebase, identifies root causes, and suggests (or applies) fixes — from the terminal or Slack.

---

## Features

- **Multi-backend observability** — CloudWatch, GCP Cloud Monitoring, Elasticsearch/OpenSearch
- **AI-powered RCA** — Claude analyzes logs, traces, and metrics to find root causes
- **Codebase analysis** — cross-references incidents with your source code to pinpoint bugs
- **Fix suggestions** — proposes diffs with Semgrep-validated safety checks
- **Human-in-the-loop** — fixes require explicit `/approve` before any code changes
- **Dual interface** — full-featured CLI and Slack bot with slash commands
- **Continuous monitoring** — background loop detects anomalies and posts to Slack proactively

---

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip
- Anthropic API key

### Install

```bash
git clone https://github.com/your-org/tinker.git
cd tinker
uv sync
cp .env.example .env
# Fill in your credentials in .env
```

### CLI Usage

```bash
# Analyze a service for incidents in the last hour
tinker analyze payments-api --since 1h --backend cloudwatch

# Show detailed root cause analysis
tinker analyze payments-api --since 2h --verbose

# Get fix suggestion for a specific incident
tinker fix INC-20240403-001

# Apply a fix after reviewing (requires --approve)
tinker fix INC-20240403-001 --approve

# Start continuous monitoring
tinker monitor --services payments-api,auth-service --channel "#incidents"
```

### Slack Bot

Invite `@tinker` to a channel, then:

```
/tinker-analyze payments-api
/tinker-fix INC-20240403-001
/tinker-approve INC-20240403-001
/tinker-status
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Interface Layer                          │
│        CLI (Typer)              Slack Bot (Bolt)             │
└───────────────────────────────────────────────────────────┬─┘
                                                            │
┌───────────────────────────────────────────────────────────▼─┐
│                   Agent Orchestration Layer                   │
│         Claude (claude-sonnet-4-6 / claude-opus-4-6)         │
│   Tools: query_logs | analyze_code | suggest_fix | apply_fix │
└──────────────────────────┬──────────────────────────────────┘
                           │
          ┌────────────────┴───────────────┐
          │                                │
┌─────────▼──────────┐         ┌───────────▼────────────┐
│  Observability     │         │  Codebase Layer         │
│  - CloudWatch      │         │  - GitHub/GitLab API    │
│  - GCP Monitoring  │         │  - AST / tree-sitter    │
│  - Elasticsearch   │         │  - Semgrep validation   │
└────────────────────┘         └────────────────────────┘
```

---

## Configuration

All configuration is via environment variables. See [.env.example](.env.example) for the full list.

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key (required) |
| `AWS_PROFILE` | AWS profile for CloudWatch (or use IAM role) |
| `AWS_REGION` | AWS region |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to GCP service account JSON |
| `ELASTICSEARCH_URL` | Elasticsearch endpoint |
| `ELASTICSEARCH_API_KEY` | Elasticsearch API key |
| `SLACK_BOT_TOKEN` | Slack bot token (`xoxb-...`) |
| `SLACK_APP_TOKEN` | Slack app token for Socket Mode (`xapp-...`) |
| `SLACK_SIGNING_SECRET` | Slack signing secret |
| `GITHUB_TOKEN` | GitHub token for PR creation |
| `TINKER_REPO_PATH` | Local path to the codebase being monitored |

---

## Security

- **No automated deploys** — `apply_fix` and `create_pr` require explicit human approval
- **RBAC** — Slack commands are gated by user group membership
- **Fix validation** — all suggested diffs are scanned with Semgrep before presentation
- **Audit log** — every agent action is logged with actor, timestamp, and approval chain
- **Secrets** — credentials are never logged or sent to the LLM; use Secrets Manager in production
- **Prompt injection defense** — log content is sanitized before being included in LLM context

---

## Development

```bash
uv sync --group dev
pytest
```

---

## License

MIT

---
sidebar_position: 2
title: Slack Bot
---

# Slack Integration

Tinker's Slack bot lets your team run observability commands and approve fixes directly from Slack — no terminal access required.

:::info Bot not in channel?
If you see `not_in_channel` errors, invite the bot to the target channel: `/invite @tinker`
:::

---

## Setup

### 1. Create a Slack app

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. Name it `Tinker` and select your workspace

### 2. Configure OAuth scopes

Under **OAuth & Permissions → Bot Token Scopes**, add:

| Scope | Purpose |
|---|---|
| `chat:write` | Post alert messages and command responses |
| `chat:write.public` | Post to public channels without joining |
| `channels:read` | List channels for routing |
| `commands` | Receive slash commands |

### 3. Create slash commands

Under **Slash Commands → Create New Command**, add each command below.

**Request URL** for all commands: `https://tinker.your-company.internal/slack/events`

| Command | Description | Usage hint |
|---|---|---|
| `/tinker-logs` | Fetch recent logs | `<service> [since=30m] [q=level:ERROR]` |
| `/tinker-anomaly` | Detect anomalies | `<service> [since=1h] [severity=high]` |
| `/tinker-analyze` | Full RCA (AI) | `<service> [since=1h]` |
| `/tinker-fix` | Propose code fix | `<session-id>` |
| `/tinker-approve` | Apply fix and open PR | `<session-id>` |
| `/tinker-watch` | Manage watches | `start <service> \| list \| stop <id>` |
| `/tinker-slo` | Show SLO status | `<service> [target=99.9] [window=30d]` |
| `/tinker-diff` | Compare time windows | `<service> [baseline=2h] [compare=1h]` |
| `/tinker-status` | Server health | — |
| `/tinker-help` | Show help | — |

### 4. Enable Event Subscriptions

Under **Event Subscriptions**:
- Toggle **Enable Events** on
- Set **Request URL** to `https://tinker.your-company.internal/slack/events`
- Wait for Slack to verify the endpoint (the server must be running)

### 5. Install to workspace

Under **Install App → Install to Workspace** → **Allow**.

Copy:
- **Bot User OAuth Token** (`xoxb-...`) — this is `SLACK_BOT_TOKEN`
- **Signing Secret** from **Basic Information** — this is `SLACK_SIGNING_SECRET`

### 6. Add secrets to server config

`tinkr init server` asks for these in Step 2. For manual setup:

```bash title="~/.tinker/.env"
SLACK_BOT_TOKEN=xoxb-xxxxxxxxxxxx-xxxxxxxxxxxx-xxxxxxxxxxxxxxxxxxxxxxxx
SLACK_SIGNING_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

```toml title="~/.tinker/config.toml"
[slack]
bot_token      = "env:SLACK_BOT_TOKEN"
signing_secret = "env:SLACK_SIGNING_SECRET"
alerts_channel = "#incidents"
```

### 7. Invite the bot to channels

In each channel where you want alerts or commands to work:

```
/invite @tinker
```

---

## Using slash commands

```
/tinker-logs payments-api since=30m q=level:ERROR
/tinker-anomaly payments-api since=1h severity=high
/tinker-analyze payments-api since=2h
/tinker-slo payments-api target=99.9 window=30d
/tinker-diff payments-api baseline=24h compare=1h
/tinker-watch start payments-api interval=120
/tinker-watch list
/tinker-watch stop watch-abc123
/tinker-approve sess-abc123
/tinker-status
/tinker-help
```

---

## Alert routing via Slack notifier

Configure Slack as a notifier in your profile so watches send alerts to channels:

```toml title="~/.tinker/config.toml"
[profiles.aws-prod.notifiers.default]
type      = "slack"
bot_token = "env:SLACK_BOT_TOKEN"
channel   = "#prod-incidents"

[profiles.aws-prod.notifiers.payments-team]
type      = "slack"
bot_token = "env:SLACK_BOT_TOKEN"
channel   = "#payments-oncall"
```

Start a watch targeting a specific notifier:

```bash
tinkr watch start payments-api --notifier payments-team
tinkr watch start payments-api --notifier default --destination "#sre-alerts"
```

### Alert message format

```
*Tinker Watch* — `payments-api`  [watch-a3f2b1c4]

• *HIGH* `error_count` — 847 errors in 10m (threshold: 10)
• *MEDIUM* `latency_p99` — 2.4s avg (threshold: 1s)
```

---

## RBAC for approve

`/tinker-approve` requires the Slack user's email to be mapped to a role with `oncall` or `sre-lead`:

```toml title="~/.tinker/config.toml"
[auth]
api_keys = [
  { hash = "<sha256>", subject = "alice@acme.com", roles = ["oncall"] }
]
```

If the Slack user is not in the `oncall` or `sre-lead` role, the approve command is rejected.

---
sidebar_position: 11
title: watch
---

# tinkr watch

Manage continuous background monitoring. A watch polls a service at a regular interval and fires a notification when the anomaly set changes.

```
tinkr watch <subcommand> [options]
```

## Subcommands

| Subcommand | Description |
|---|---|
| `start <service>` | Start a new watch |
| `list` | List all watches |
| `stop <watch-id>` | Stop a watch (keeps it in the list as `stopped`) |
| `delete <watch-id>` | Hard-delete a watch and remove it from the list |

---

## `tinkr watch start`

```bash
tinkr watch start <service> [options]
```

### Options

| Flag | Default | Description |
|---|---|---|
| `--interval INT` | `60` | Poll interval in seconds |
| `--notifier TEXT` | `default` | Notifier name from your profile config |
| `--destination TEXT` | — | Override the notifier's channel/URL for this watch |

### Examples

```bash
# Start with defaults (60s interval, default notifier)
tinkr watch start payments-api

# Faster polling, specific Slack channel
tinkr watch start payments-api --interval 30 --notifier slack-ops --destination "#payments-oncall"

# PagerDuty
tinkr watch start payments-api --notifier pagerduty

# Discord
tinkr watch start auth-service --notifier discord-ops
```

### Output

```
Watch started
  ID:       watch-a3f2b1c4
  Service:  payments-api
  Interval: 60s
  Notifier: pagerduty
```

---

## `tinkr watch list`

```bash
tinkr watch list
```

### Output

```
WATCH ID          SERVICE         INTERVAL  STATUS   NOTIFIER    LAST CHECK
watch-a3f2b1c4   payments-api    60s       running  pagerduty   14:01:03
watch-b5c6d7e8   auth-service    120s      running  slack-ops   14:00:48
watch-9f8e7d6c   inventory-api   60s       stopped  default     13:45:12
```

---

## `tinkr watch stop`

Stops the watch (marks it `stopped`) but keeps it in the database.

```bash
tinkr watch stop watch-a3f2b1c4
```

You can restart a stopped watch by calling `tinkr watch start` again for the same service.

---

## `tinkr watch delete`

Permanently removes the watch from the database. Use this after stopping a watch you no longer need.

```bash
tinkr watch delete watch-a3f2b1c4
```

:::warning
This is a hard delete. The watch will not appear in `tinkr watch list` and cannot be recovered.
:::

---

## Notification triggers

A notification fires only when the anomaly set **changes** — new anomalies appear, or existing ones resolve. This prevents alert fatigue from repeated identical notifications on every poll tick.

### Anomaly change detection

Tinkr computes a SHA-256 hash of the current anomaly set. If the hash differs from the previous poll, a notification is sent.

### Notification message (Slack example)

```
*Tinkr Watch* — `payments-api`  [watch-a3f2b1c4]

• *HIGH* `error_count` — 847 errors in 10m (threshold: 10)
• *MEDIUM* `latency_p99` — 2.4s avg (threshold: 1s)
```

---

## Configuring notifiers

Notifiers are configured per-profile in `~/.tinkr/config.toml`:

```toml
[profiles.aws-prod.notifiers.pagerduty]
type                 = "webhook"
url                  = "env:PAGERDUTY_WEBHOOK_URL"
header_Authorization = "env:PAGERDUTY_API_KEY"

[profiles.aws-prod.notifiers.slack-ops]
type      = "slack"
bot_token = "env:SLACK_BOT_TOKEN"
channel   = "#prod-incidents"

[profiles.aws-prod.notifiers.discord-ops]
type        = "discord"
webhook_url = "env:DISCORD_OPS_WEBHOOK_URL"
```

See [Webhooks](../integrations/webhooks) and [Slack](../integrations/slack) for full notifier configuration.

---

## Persistence

Watches persist across server restarts. The watch state is stored in `~/.tinkr/tinker.db` (SQLite). When the Tinkr server starts, it resumes all watches that were `running` at shutdown.

## See also

- [`tinkr alert`](alert) — threshold-based rules (complement to watches)
- [Slack Integration](../integrations/slack) — alert routing via Slack
- [Webhooks](../integrations/webhooks) — webhook notifier configuration

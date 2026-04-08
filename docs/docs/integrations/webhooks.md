---
sidebar_position: 3
title: Webhooks
---

# Webhook and Callback Integration

Tinker can send anomaly alerts to any HTTP endpoint — PagerDuty, Opsgenie, custom receivers, or your own alerting pipeline.

---

## Webhook notifier

Configure a webhook notifier in your profile:

```toml title="~/.tinkr/config.toml"
[profiles.aws-prod.notifiers.pagerduty]
type                 = "webhook"
url                  = "env:PAGERDUTY_WEBHOOK_URL"
header_Authorization = "env:PAGERDUTY_API_KEY"

[profiles.aws-prod.notifiers.opsgenie]
type                 = "webhook"
url                  = "env:OPSGENIE_WEBHOOK_URL"
header_Authorization = "env:OPSGENIE_API_KEY"

[profiles.aws-prod.notifiers.custom]
type = "webhook"
url  = "https://hooks.internal.acme.com/tinker-alerts"
```

```bash title="~/.tinkr/.env"
PAGERDUTY_WEBHOOK_URL=https://events.pagerduty.com/integration/XXXX/enqueue
PAGERDUTY_API_KEY=Token token=XXXX
OPSGENIE_WEBHOOK_URL=https://api.opsgenie.com/v2/alerts
OPSGENIE_API_KEY=GenieKey XXXX
```

---

## Webhook payload

Tinker sends a `POST` with `Content-Type: application/json`:

```json
{
  "watch_id": "watch-a3f2b1c4",
  "service": "payments-api",
  "anomaly_count": 2,
  "anomalies": [
    {
      "service": "payments-api",
      "metric": "error_count",
      "description": "847 errors in 10m, exceeds threshold of 10",
      "severity": "high",
      "detected_at": "2026-04-07T14:01:03Z",
      "current_value": 847.0,
      "threshold": 10.0
    },
    {
      "service": "payments-api",
      "metric": "latency_p99",
      "description": "p99 latency 3.2s exceeds threshold of 1s",
      "severity": "medium",
      "detected_at": "2026-04-07T14:01:05Z",
      "current_value": 3200.0,
      "threshold": 1000.0
    }
  ]
}
```

The webhook fires only when the anomaly set **changes** (not on every poll tick).

---

## Discord notifier

```toml title="~/.tinkr/config.toml"
[profiles.aws-prod.notifiers.discord-ops]
type        = "discord"
webhook_url = "env:DISCORD_OPS_WEBHOOK_URL"
```

```bash title="~/.tinkr/.env"
DISCORD_OPS_WEBHOOK_URL=https://discord.com/api/webhooks/1234567890/xxxx
```

Discord webhooks do not require a destination override — the channel is fixed in the URL.

---

## Using webhook notifiers with watches

```bash
tinkr watch start payments-api --notifier pagerduty
tinkr watch start auth-service --notifier discord-ops
tinkr watch start inventory-api --notifier custom --destination "https://hooks.acme.com/inventory"
```

The `--destination` flag overrides the webhook URL for notifiers that support it.

---

## PagerDuty integration

Tinker's webhook payload maps cleanly to PagerDuty Events API v2:

```python title="Custom PagerDuty adapter (example)"
import httpx

ROUTING_KEY = "your-pagerduty-routing-key"

async def forward_to_pagerduty(tinker_payload: dict):
    for anomaly in tinker_payload["anomalies"]:
        severity_map = {"critical": "critical", "high": "error", "medium": "warning", "low": "info"}
        await httpx.AsyncClient().post(
            "https://events.pagerduty.com/v2/enqueue",
            json={
                "routing_key": ROUTING_KEY,
                "event_action": "trigger",
                "payload": {
                    "summary": anomaly["description"],
                    "severity": severity_map.get(anomaly["severity"], "warning"),
                    "source": anomaly["service"],
                    "custom_details": anomaly,
                },
            },
        )
```

---

## REST API (direct access)

You can call the Tinker server directly from any system:

```bash
# Detect anomalies
curl -X POST https://tinker.acme.internal/api/v1/anomalies \
  -H "Authorization: Bearer $TINKR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"service": "payments-api", "window_minutes": 10}'

# Stream RCA
curl -X POST https://tinker.acme.internal/api/v1/rca \
  -H "Authorization: Bearer $TINKR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{"service": "payments-api", "since": "1h"}'

# Get SLO
curl -X POST https://tinker.acme.internal/api/v1/slo \
  -H "Authorization: Bearer $TINKR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"service": "payments-api", "target_pct": 99.9, "window": "30d"}'
```

Interactive API docs: `https://tinker.acme.internal/docs`

---

## Claude Code (remote MCP)

Add Tinker as an MCP server in `.claude/settings.json` to give Claude Code direct access to your observability tools:

```json title=".claude/settings.json"
{
  "mcpServers": {
    "tinker": {
      "transport": "sse",
      "url": "https://tinker.acme.internal/mcp/sse",
      "headers": {
        "Authorization": "Bearer ${TINKR_API_TOKEN}"
      }
    }
  }
}
```

Claude can then call `query_logs`, `get_metrics`, `detect_anomalies`, `search_code`, and `suggest_fix` directly from your editor.

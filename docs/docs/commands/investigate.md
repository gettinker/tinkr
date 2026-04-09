---
sidebar_position: 8
title: investigate
---

# tinkr investigate

Start an interactive REPL (read-eval-print loop) for AI-powered root-cause analysis. The session maintains context across multiple turns, letting you iteratively explore an incident.

```
tinkr investigate <service> [options]
```

## Arguments

| Argument | Description |
|---|---|
| `service` | Service name as configured in the active profile |

## Options

| Flag | Default | Description |
|---|---|---|
| `--since TEXT` | `1h` | Initial look-back window |
| `--session TEXT` | — | Resume a previous session by ID |

## Starting a session

```bash
tinkr investigate payments-api
tinkr investigate payments-api --since 2h
```

Tinkr immediately fetches anomalies and recent logs, then opens the REPL:

```
Investigating payments-api  [sess-a3f2b1c4]
Anomalies: 2 (HIGH: error_count, MEDIUM: latency_p99)

tinkr> _
```

## REPL commands

| Command | Description |
|---|---|
| `explain` | Explain the most likely root cause based on logs and anomalies |
| `logs [filter]` | Fetch and display recent logs, with optional filter |
| `metrics [metric]` | Display a metric chart |
| `trace` | Show distributed traces for this service |
| `fix` | Propose a code fix for the identified issue |
| `approve` | Apply the proposed fix and open a GitHub pull request |
| `context <text>` | Add manual context (e.g. "we just deployed v1.4.2") |
| `reset` | Clear the conversation and start over |
| `session` | Print the current session ID |
| `exit` | Exit the REPL |

## Example session

```
tinkr> explain

  Root cause: Stripe API is returning timeouts (HTTP 504) on charge requests.
  The error rate spike (847 errors in 10m) correlates exactly with Stripe's
  status page incident at 14:00 UTC.

  Classification: dependency_down
  Confidence: high

tinkr> logs level:ERROR

  [14:01:03] Stripe::Timeout charge_id=ch_abc123 attempt=3/3
  [14:01:04] Stripe::Timeout charge_id=ch_def456 attempt=3/3

tinkr> context "Stripe status page shows incident since 13:55 UTC"

  Context added. Updating analysis...

  This confirms the external dependency failure. No code change needed.
  Recommend: retry queue + circuit breaker for Stripe calls.

tinkr> fix

  Proposed fix: add exponential backoff with jitter to the Stripe charge call.

  --- src/payments/stripe_client.py
  +++ src/payments/stripe_client.py
  @@ -42,7 +42,12 @@
  -    response = stripe.Charge.create(**params)
  +    for attempt in range(1, 4):
  +        try:
  +            response = stripe.Charge.create(**params)
  +            break
  +        except stripe.error.Timeout:
  +            if attempt == 3: raise
  +            time.sleep(2 ** attempt + random.uniform(0, 1))

  Apply this fix? Type `approve` to open a PR.

tinkr> approve

  Branch: tinkr/fix-a3f2b1c4
  PR:     https://github.com/acme/payments/pull/247
  Status: open — awaiting review
```

## Resuming a session

```bash
# Get the session ID
tinkr investigate payments-api
# tinker> session
# sess-a3f2b1c4

# Resume later
tinkr investigate payments-api --session sess-a3f2b1c4
```

Sessions are stored in `~/.tinkr/tinker.db` and persist between terminal sessions.

## Approval requirements

`approve` requires the `oncall` or `sre-lead` role on your API key. If your key lacks the role, the command is rejected.

GitHub integration must be configured for `fix` and `approve` to work. See [GitHub Integration](../integrations/github).

## See also

- [`tinkr rca`](rca) — non-interactive streaming RCA (better for automation)
- [`tinkr fix`](investigate) — `fix` is a REPL subcommand, not a standalone command
- [GitHub Integration](../integrations/github)

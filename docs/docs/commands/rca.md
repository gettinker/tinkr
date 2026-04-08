---
sidebar_position: 9
title: rca
---

# tinker rca

Stream a full AI root-cause analysis report to the terminal. Non-interactive — outputs a structured report and exits. Suitable for automation, runbooks, and CI pipelines.

```
tinker rca <service> [options]
```

## Arguments

| Argument | Description |
|---|---|
| `service` | Service name as configured in the active profile |

## Options

| Flag | Default | Description |
|---|---|---|
| `--since TEXT` | `1h` | Look-back window for log and metric queries |
| `--json` | off | Emit NDJSON event stream instead of formatted output |

## Examples

```bash
# RCA for the last hour
tinker rca payments-api

# Deep analysis over 2 hours
tinker rca payments-api --since 2h

# JSON stream (for log aggregation or scripts)
tinker rca payments-api --since 1h --json
```

## Output sections

The report is streamed section-by-section as it is generated:

```
## Root Cause Analysis — payments-api
Generated: 2026-04-07 14:05 UTC   Window: 1h

### 1. Summary
Stripe API is returning HTTP 504 timeouts on charge requests. 847 errors
detected in 10 minutes, correlating with a Stripe platform incident.

### 2. Anomalies
• HIGH     error_count   847 errors (threshold: 10)
• MEDIUM   latency_p99   3.2s (threshold: 1s)

### 3. Root Cause
Classification: dependency_down
The error pattern started at 14:00:51 UTC, matching Stripe's incident
start time. No code changes were deployed in the 30 minutes prior.
Stripe charge calls are timing out after 30s on all attempts.

### 4. Relevant Code
File: src/payments/stripe_client.py  lines 40–55
The charge call has no retry logic. Three sequential attempts all fail
with no backoff, causing each failed payment to hold a connection for 90s.

### 5. Recommended Actions
1. Add exponential-backoff retry to Stripe calls (3 attempts, max 16s)
2. Implement a circuit breaker — open after 10 failures in 60s
3. Add Stripe status to the health check endpoint
4. Page Stripe support if the incident exceeds 30 minutes

### 6. Confidence
High — external dependency failure confirmed by log pattern and timing.
No internal code change or configuration drift detected.
```

## Model selection

For high-severity incidents, Tinkr automatically switches to `claude-opus-4-6` with extended thinking for deeper analysis. For routine checks, it uses `claude-sonnet-4-6`.

## JSON stream format

With `--json`, each section is emitted as a newline-delimited JSON event:

```json
{"type": "section", "title": "Summary", "content": "..."}
{"type": "section", "title": "Anomalies", "content": "..."}
{"type": "section", "title": "Root Cause", "content": "..."}
{"type": "done"}
```

## Difference from `tinker investigate`

| | `tinker rca` | `tinker investigate` |
|---|---|---|
| Mode | Non-interactive, streaming | Interactive REPL |
| Use case | Automation, runbooks, PagerDuty webhooks | Human-led incident response |
| Fix + approve | Not available | Available |
| Output | Structured 6-section report | Conversational |

## See also

- [`tinker investigate`](investigate) — interactive investigation with fix and approve
- [`tinker anomaly`](anomaly) — list anomalies without full analysis

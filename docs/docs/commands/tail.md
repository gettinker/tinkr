---
sidebar_position: 3
title: tail
---

# tinkr tail

Stream live logs to your terminal. Polls the backend continuously and prints new lines as they arrive.

```
tinkr tail <service> [options]
```

## Arguments

| Argument | Description |
|---|---|
| `service` | Service name as configured in the active profile |

## Options

| Flag | Default | Description |
|---|---|---|
| `--filter TEXT` | — | Filter expression (e.g. `level:ERROR`) |
| `--interval INT` | `5` | Poll interval in seconds |

## Examples

```bash
# Stream all logs
tinkr tail payments-api

# Errors only
tinkr tail payments-api --filter level:ERROR

# Fast polling
tinkr tail payments-api --interval 2
```

## Output

Logs are printed as they arrive:

```
[14:01:03] ERROR  Payment charge failed: card_declined
[14:01:04] ERROR  Stripe API timeout after 30s
[14:01:09] INFO   Health check OK
[14:01:14] ERROR  Payment charge failed: insufficient_funds
```

Press `Ctrl+C` to stop.

## Notes

- `tinkr tail` is implemented as a polling loop, not a true WebSocket stream. Most observability backends do not expose a push-based log stream.
- For high-volume services, use `--filter` to reduce noise.
- The `--interval` floor is `1` second — lower values may overwhelm the backend.

## See also

- [`tinkr logs`](logs) — non-streaming log fetch
- [`tinkr investigate`](investigate) — AI investigation starting from recent logs

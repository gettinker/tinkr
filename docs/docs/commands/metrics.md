---
sidebar_position: 4
title: metrics
---

# tinkr metrics

Query a metric time series from a service.

```
tinkr metrics <service> [options]
```

## Arguments

| Argument | Description |
|---|---|
| `service` | Service name as configured in the active profile |

## Options

| Flag | Default | Description |
|---|---|---|
| `--metric TEXT` | `http_requests_total` | Metric name to query |
| `--since TEXT` | `1h` | Look-back window — e.g. `30m`, `2h`, `24h` |
| `--json` | off | Emit raw JSON |

## Examples

```bash
# HTTP request rate (last hour)
tinkr metrics payments-api

# P99 latency
tinkr metrics payments-api --metric latency_p99 --since 2h

# Error rate over 24h
tinkr metrics payments-api --metric error_rate --since 24h

# Raw JSON for scripting
tinkr metrics payments-api --metric cpu_usage --since 30m --json
```

## Output

```
Metric: http_requests_total  Service: payments-api  Window: 1h

  14:00  ████████████████████ 1240 req/min
  14:05  ████████████████████ 1318 req/min
  14:10  ████████████████████ 1201 req/min
  14:15  ██████████░░░░░░░░░░  620 req/min   ← drop
  14:20  ████░░░░░░░░░░░░░░░░  230 req/min
```

## Common metrics by backend

### Grafana / Prometheus

```
http_requests_total
http_request_duration_seconds
process_cpu_seconds_total
go_memstats_heap_alloc_bytes
```

### CloudWatch

```
RequestCount
Latency
5XXError
CPUUtilization
```

### Datadog

```
trace.web.request
trace.web.request.duration
aws.ecs.cpuutilization
```

### Azure Monitor

```
requests/count
requests/duration
requests/failed
```

## See also

- [`tinkr anomaly`](anomaly) — automatically detect unusual metric behavior
- [`tinkr diff`](diff) — compare metric values between two time windows

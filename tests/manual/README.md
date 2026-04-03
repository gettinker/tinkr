# Manual Test Stack

Local end-to-end environment for testing Tinker without a cloud account.

## What's included

| Service | Port | Purpose |
|---|---|---|
| `tinker` | 8000 | Tinker agent server (grafana backend) |
| `payments-api` | 7000 | Dummy microservice — emits logs at all levels + Prometheus metrics |
| `loki` | 3100 | Log storage |
| `prometheus` | 9090 | Metrics storage (scrapes tinker + payments-api) |
| `grafana` | 3000 | Visual UI for logs/metrics exploration |

## Quick start

```bash
cd tests/manual

# First time: copy and fill in the env file
cp ../../.env.example .env
# edit .env — only ANTHROPIC_API_KEY is required

./run.sh
```

## Generate traffic

```bash
# Steady mixed traffic until Ctrl-C
./generate_traffic.sh

# 100 rapid requests then exit
./generate_traffic.sh burst

# Simulate an incident (error spike + circuit breaker)
./generate_traffic.sh incident

# Quiet — only ok + debug
./generate_traffic.sh quiet
```

## Analyze with Tinker

```bash
export TINKER_SERVER_URL=http://localhost:8000

# After running generate_traffic.sh incident:
tinker analyze payments-api --since 5m -v

# Standard analysis
tinker analyze payments-api --since 1h
```

## payments-api endpoints

| Endpoint | What it does |
|---|---|
| `GET /health` | Health check |
| `GET /metrics` | Prometheus metrics |
| `GET /pay` | Random weighted scenario |
| `GET /pay/ok` | Successful payment (INFO log) |
| `GET /pay/error` | Failed payment (ERROR log) |
| `GET /pay/slow` | Slow DB query (WARN log) |
| `GET /pay/warn` | Retry scenario (WARN log) |
| `GET /pay/critical` | Circuit breaker open (CRITICAL log) |
| `GET /pay/debug` | Cache hit (DEBUG log) |

## Tear down

```bash
./run.sh down
```

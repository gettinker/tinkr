#!/usr/bin/env bash
# Hit the dummy payments-api to generate realistic traffic with mixed log levels.
#
# Usage:
#   ./generate_traffic.sh                  # steady mixed traffic (Ctrl-C to stop)
#   ./generate_traffic.sh burst            # 100 rapid mixed requests then exit
#   ./generate_traffic.sh incident         # simulate an incident spike (errors + critical)
#   ./generate_traffic.sh quiet            # just ok + debug, no errors

set -euo pipefail

BASE_URL="${PAYMENTS_API_URL:-http://localhost:7000}"
MODE="${1:-steady}"

# ── helpers ────────────────────────────────────────────────────────────────────

ok()       { curl -sf "$BASE_URL/pay/ok"       > /dev/null; }
error()    { curl -sf "$BASE_URL/pay/error"    > /dev/null || true; }
slow()     { curl -sf "$BASE_URL/pay/slow"     > /dev/null; }
warn()     { curl -sf "$BASE_URL/pay/warn"     > /dev/null; }
critical() { curl -sf "$BASE_URL/pay/critical" > /dev/null || true; }
debug_()   { curl -sf "$BASE_URL/pay/debug"    > /dev/null; }
mixed()    { curl -sf "$BASE_URL/pay"          > /dev/null || true; }

status() { printf "\r\033[K  %-12s requests: %d" "$1" "$2"; }

# ── wait for server ─────────────────────────────────────────────────────────

echo -n "Checking $BASE_URL/health "
for i in $(seq 1 10); do
  if curl -sf "$BASE_URL/health" > /dev/null 2>&1; then
    echo "✓"
    break
  fi
  echo -n "."
  sleep 1
  if [[ $i == 10 ]]; then
    echo ""
    echo "payments-api not reachable at $BASE_URL — is the stack running?"
    exit 1
  fi
done

# ── modes ──────────────────────────────────────────────────────────────────────

case "$MODE" in

  burst)
    echo "Sending 100 mixed requests..."
    count=0
    for i in $(seq 1 100); do
      mixed
      count=$((count + 1))
      status "burst" $count
    done
    echo ""
    echo "Done. Check Grafana or run: tinker analyze payments-api --since 5m"
    ;;

  incident)
    echo "Simulating incident: error spike + critical alert"
    echo ""

    echo "Phase 1 — baseline (10 ok requests)"
    for i in $(seq 1 10); do ok; sleep 0.2; done

    echo ""
    echo "Phase 2 — error ramp (20 errors + 5 warnings)"
    for i in $(seq 1 20); do error; sleep 0.1; done
    for i in $(seq 1 5);  do warn;  sleep 0.1; done

    echo ""
    echo "Phase 3 — critical: circuit breaker opens"
    critical
    sleep 1
    critical

    echo ""
    echo "Phase 4 — service unavailable (10 more errors)"
    for i in $(seq 1 10); do error; sleep 0.2; done

    echo ""
    echo "Incident simulation done."
    echo "Run: tinker analyze payments-api --since 5m -v"
    ;;

  quiet)
    echo "Quiet mode — ok + debug only (Ctrl-C to stop)..."
    count=0
    while true; do
      ok
      debug_
      count=$((count + 2))
      status "quiet" $count
      sleep 1
    done
    ;;

  steady | *)
    echo "Steady mixed traffic (Ctrl-C to stop)..."
    echo "Weights: 60% ok  15% error  10% warn  10% slow  5% debug"
    echo ""
    count=0
    while true; do
      # Weighted selection via modulo of a random number
      r=$((RANDOM % 100))
      if   [[ $r -lt 60 ]]; then ok
      elif [[ $r -lt 75 ]]; then error
      elif [[ $r -lt 85 ]]; then warn
      elif [[ $r -lt 95 ]]; then slow
      else                        debug_
      fi
      count=$((count + 1))
      status "steady" $count
      sleep 0.5
    done
    ;;

esac

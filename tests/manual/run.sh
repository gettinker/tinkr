#!/usr/bin/env bash
# Start the full manual test stack.
#
# Usage:
#   cd tests/manual
#   ./run.sh          # start everything
#   ./run.sh down     # tear down
#   ./run.sh logs     # tail all logs

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE=".env"
if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "../../.env.example" ]]; then
    cp ../../.env.example "$ENV_FILE"
    echo "Created $ENV_FILE from .env.example — set ANTHROPIC_API_KEY before continuing."
    exit 1
  else
    echo "No .env file found. Create tests/manual/.env with at least:"
    echo "  ANTHROPIC_API_KEY=sk-ant-..."
    exit 1
  fi
fi

if [[ "${1:-}" == "down" ]]; then
  docker compose down --remove-orphans
  exit 0
fi

if [[ "${1:-}" == "logs" ]]; then
  docker compose logs -f
  exit 0
fi

echo "Starting manual test stack..."
docker compose up --build -d

echo ""
echo "Waiting for services to be healthy..."
sleep 5

# Wait for payments-api to be healthy
echo -n "payments-api "
for i in $(seq 1 20); do
  if curl -sf http://localhost:7000/health > /dev/null 2>&1; then
    echo "✓ healthy"
    break
  fi
  echo -n "."
  sleep 2
done

echo ""
echo "──────────────────────────────────────────────"
echo "  Tinker server   → http://localhost:8000"
echo "  payments-api    → http://localhost:7000"
echo "  Grafana UI      → http://localhost:3000"
echo "  Prometheus      → http://localhost:9090"
echo "  Loki            → http://localhost:3100"
echo ""
echo "  Seed traffic:   ./generate_traffic.sh"
echo "  Tinker analyze: tinker analyze payments-api"
echo "──────────────────────────────────────────────"

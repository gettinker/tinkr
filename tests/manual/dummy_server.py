"""Dummy HTTP server for manual testing.

Simulates a realistic microservice (payments-api) that:
- Emits structured logs at all levels via Loki push API
- Exposes Prometheus metrics scraped by Prometheus
- Has endpoints that deliberately trigger errors/latency for testing Tinker

Usage:
    python dummy_server.py          # runs on :7000, pushes logs to localhost:3100
    LOKI_URL=http://loki:3100 python dummy_server.py  (inside docker)
"""

from __future__ import annotations

import os
import random
import time
import threading
import json
import logging
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.request import urlopen, Request
from urllib.parse import urlparse, parse_qs

# ── Config ────────────────────────────────────────────────────────────────────

PORT = int(os.environ.get("PORT", 7000))
SERVICE_NAME = os.environ.get("SERVICE_NAME", "payments-api")
LOKI_URL = os.environ.get("LOKI_URL", "http://localhost:3100")

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(message)s")
log = logging.getLogger(SERVICE_NAME)

# ── Prometheus metrics (hand-rolled, no dependencies) ────────────────────────

_metrics: dict[str, float] = {
    "http_requests_total": 0,
    "http_errors_total": 0,
    "http_request_duration_seconds_sum": 0.0,
    "http_request_duration_seconds_count": 0,
    "db_query_duration_seconds_sum": 0.0,
    "db_query_duration_seconds_count": 0,
    "payment_processed_total": 0,
    "payment_failed_total": 0,
    "active_connections": 0,
}


def _prom_text() -> str:
    lines = []
    lines.append("# HELP http_requests_total Total HTTP requests")
    lines.append("# TYPE http_requests_total counter")
    lines.append(f'http_requests_total{{service="{SERVICE_NAME}"}} {_metrics["http_requests_total"]}')

    lines.append("# HELP http_errors_total Total HTTP 5xx errors")
    lines.append("# TYPE http_errors_total counter")
    lines.append(f'http_errors_total{{service="{SERVICE_NAME}"}} {_metrics["http_errors_total"]}')

    lines.append("# HELP http_request_duration_seconds HTTP request latency")
    lines.append("# TYPE http_request_duration_seconds summary")
    count = _metrics["http_request_duration_seconds_count"]
    s = _metrics["http_request_duration_seconds_sum"]
    lines.append(f'http_request_duration_seconds_sum{{service="{SERVICE_NAME}"}} {s:.4f}')
    lines.append(f'http_request_duration_seconds_count{{service="{SERVICE_NAME}"}} {count}')

    lines.append("# HELP payment_processed_total Payments successfully processed")
    lines.append("# TYPE payment_processed_total counter")
    lines.append(f'payment_processed_total{{service="{SERVICE_NAME}"}} {_metrics["payment_processed_total"]}')

    lines.append("# HELP payment_failed_total Payments that failed")
    lines.append("# TYPE payment_failed_total counter")
    lines.append(f'payment_failed_total{{service="{SERVICE_NAME}"}} {_metrics["payment_failed_total"]}')

    lines.append("# HELP active_connections Current active connections")
    lines.append("# TYPE active_connections gauge")
    lines.append(f'active_connections{{service="{SERVICE_NAME}"}} {_metrics["active_connections"]}')

    return "\n".join(lines) + "\n"


# ── Loki log pusher ───────────────────────────────────────────────────────────

def _push_loki(level: str, message: str, extra: dict | None = None) -> None:
    """Push a single log line to Loki via the push API."""
    ts_ns = str(int(time.time() * 1e9))
    fields = {"level": level, "service": SERVICE_NAME, "message": message}
    if extra:
        fields.update(extra)
    line = json.dumps(fields)

    payload = {
        "streams": [{
            "stream": {"service": SERVICE_NAME, "level": level},
            "values": [[ts_ns, line]],
        }]
    }
    body = json.dumps(payload).encode()
    req = Request(
        f"{LOKI_URL}/loki/api/v1/push",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urlopen(req, timeout=2)
    except Exception as exc:
        log.warning("Loki push failed: %s", exc)


def emit(level: str, message: str, extra: dict | None = None) -> None:
    """Emit to both Python logger and Loki."""
    getattr(log, level.lower(), log.info)(message)
    _push_loki(level.upper(), message, extra)


# ── Request scenarios ─────────────────────────────────────────────────────────

PAYMENT_METHODS = ["card", "wallet", "bank_transfer", "crypto"]
CURRENCIES = ["USD", "EUR", "GBP", "INR"]
ERROR_MESSAGES = [
    "database connection timeout after 30s",
    "upstream fraud-check service returned 503",
    "card tokenization service unavailable",
    "insufficient funds in settlement account",
    "payment gateway TLS handshake failed",
    "duplicate transaction detected: idempotency key reused",
    "rate limit exceeded on provider stripe: 429",
    "serialization error: unexpected null in amount field",
]
SLOW_QUERY_MESSAGES = [
    "SELECT * FROM transactions WHERE user_id=? AND status='pending' took 4.2s (expected <100ms)",
    "UPDATE payment_intents SET status='processing' took 2.8s — lock contention suspected",
    "full table scan on invoices table — missing index on created_at",
]
USER_IDS = [f"usr_{i:06d}" for i in range(1, 200)]
TXN_IDS  = [f"txn_{random.randint(100000, 999999)}" for _ in range(500)]


def _scenario_ok() -> tuple[int, str]:
    uid = random.choice(USER_IDS)
    txn = random.choice(TXN_IDS)
    amount = round(random.uniform(1, 5000), 2)
    currency = random.choice(CURRENCIES)
    method = random.choice(PAYMENT_METHODS)
    latency = random.uniform(0.02, 0.25)
    time.sleep(latency)

    emit("INFO", f"payment processed", {
        "user_id": uid, "transaction_id": txn,
        "amount": amount, "currency": currency,
        "method": method, "duration_ms": round(latency * 1000),
    })
    _metrics["payment_processed_total"] += 1
    _metrics["http_request_duration_seconds_sum"] += latency
    _metrics["http_request_duration_seconds_count"] += 1
    return 200, json.dumps({"status": "ok", "transaction_id": txn})


def _scenario_error() -> tuple[int, str]:
    uid = random.choice(USER_IDS)
    txn = random.choice(TXN_IDS)
    msg = random.choice(ERROR_MESSAGES)
    emit("ERROR", f"payment failed: {msg}", {
        "user_id": uid, "transaction_id": txn,
        "error": msg,
    })
    _metrics["payment_failed_total"] += 1
    _metrics["http_errors_total"] += 1
    _metrics["http_request_duration_seconds_count"] += 1
    return 500, json.dumps({"status": "error", "error": msg})


def _scenario_slow() -> tuple[int, str]:
    uid = random.choice(USER_IDS)
    latency = random.uniform(2.5, 6.0)
    msg = random.choice(SLOW_QUERY_MESSAGES)
    emit("WARN", f"slow database query detected: {msg}", {
        "user_id": uid, "duration_ms": round(latency * 1000),
    })
    time.sleep(min(latency, 0.5))   # don't actually block that long
    _metrics["http_request_duration_seconds_sum"] += latency
    _metrics["http_request_duration_seconds_count"] += 1
    return 200, json.dumps({"status": "ok", "warning": "slow"})


def _scenario_warn() -> tuple[int, str]:
    uid = random.choice(USER_IDS)
    retry_count = random.randint(1, 4)
    emit("WARN", f"retrying payment authorization (attempt {retry_count}/5)", {
        "user_id": uid, "retry": retry_count,
    })
    _metrics["http_request_duration_seconds_count"] += 1
    return 200, json.dumps({"status": "ok", "retried": retry_count})


def _scenario_critical() -> tuple[int, str]:
    emit("CRITICAL", "circuit breaker OPEN: downstream fraud-service unreachable for 60s", {
        "circuit": "fraud-service", "state": "open",
    })
    emit("ERROR", "all payment processing halted until circuit breaker resets", {})
    _metrics["http_errors_total"] += 5
    return 503, json.dumps({"status": "error", "error": "service unavailable"})


def _scenario_debug() -> tuple[int, str]:
    uid = random.choice(USER_IDS)
    emit("DEBUG", f"cache hit for user profile {uid}", {"user_id": uid, "cache": "redis"})
    emit("DEBUG", f"JWT validated, expiry in 3540s", {"user_id": uid})
    return 200, json.dumps({"status": "ok"})


# ── HTTP handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # silence default access log
        pass

    def _respond(self, status: int, body: str) -> None:
        encoded = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        _metrics["http_requests_total"] += 1
        _metrics["active_connections"] += 1

        try:
            path = urlparse(self.path).path

            if path == "/metrics":
                body = _prom_text()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body.encode())

            elif path == "/health":
                self._respond(200, json.dumps({"status": "ok", "service": SERVICE_NAME}))

            elif path == "/pay":
                # Weighted random scenario
                scenario = random.choices(
                    [_scenario_ok, _scenario_warn, _scenario_slow, _scenario_error, _scenario_debug],
                    weights=[60, 10, 10, 15, 5],
                )[0]
                status, body = scenario()
                self._respond(status, body)

            elif path == "/pay/ok":
                self._respond(*_scenario_ok())
            elif path == "/pay/error":
                self._respond(*_scenario_error())
            elif path == "/pay/slow":
                self._respond(*_scenario_slow())
            elif path == "/pay/warn":
                self._respond(*_scenario_warn())
            elif path == "/pay/critical":
                self._respond(*_scenario_critical())
            elif path == "/pay/debug":
                self._respond(*_scenario_debug())

            else:
                self._respond(404, json.dumps({"error": "not found"}))

        finally:
            _metrics["active_connections"] -= 1


# ── Background noise generator ────────────────────────────────────────────────

def _background_noise() -> None:
    """Emit periodic lifecycle logs so Loki always has some data."""
    startup_messages = [
        ("INFO",  "payments-api starting up"),
        ("INFO",  "connected to PostgreSQL at db:5432 (pool_size=20)"),
        ("INFO",  "connected to Redis at cache:6379"),
        ("INFO",  "loaded 142 active merchant configurations"),
        ("INFO",  "HTTP server listening on :7000"),
        ("DEBUG", "feature flag ENABLE_3DS2=true"),
        ("DEBUG", "feature flag INSTANT_PAYOUT=false"),
    ]
    for level, msg in startup_messages:
        emit(level, msg)
        time.sleep(0.1)

    while True:
        time.sleep(random.uniform(30, 90))
        emit("DEBUG", "connection pool stats", {
            "pool_size": 20,
            "active": random.randint(1, 15),
            "idle": random.randint(1, 10),
        })


if __name__ == "__main__":
    print(f"[{SERVICE_NAME}] starting on :{PORT} — Loki at {LOKI_URL}")
    threading.Thread(target=_background_noise, daemon=True).start()
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()

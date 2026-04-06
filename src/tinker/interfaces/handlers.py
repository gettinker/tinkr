"""Shared command handlers — the canonical implementation of every Tinker command.

Both the CLI and the Slack bot call these functions.
They accept a RemoteClient and return typed data — no rendering, no output, no Slack.

Adding a new command
--------------------
1. Add a handler function here.
2. Wire it in cli.py (Typer command + renderer call).
3. Wire it in slack_bot.py (Bolt slash command + Slack block formatting).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, AsyncGenerator

if TYPE_CHECKING:
    from tinker.backends.base import Anomaly, LogEntry, MetricPoint
    from tinker.client.remote import RemoteClient


# ── Time parsing ──────────────────────────────────────────────────────────────

def parse_since(since: str) -> tuple[datetime, int]:
    """Parse a since string into ``(start_datetime, window_minutes)``.

    Supported units: ``m`` (minutes), ``h`` (hours), ``d`` (days).
    Examples: ``"30m"``, ``"2h"``, ``"1d"``
    """
    unit = since[-1]
    try:
        value = int(since[:-1])
    except ValueError:
        raise ValueError(f"Invalid since value '{since}' — expected e.g. '30m', '2h', '1d'")
    match unit:
        case "m":
            return datetime.now(timezone.utc) - timedelta(minutes=value), value
        case "h":
            return datetime.now(timezone.utc) - timedelta(hours=value), value * 60
        case "d":
            return datetime.now(timezone.utc) - timedelta(days=value), value * 1440
        case _:
            raise ValueError(f"Unknown time unit '{unit}' in '{since}' — use m/h/d")


# ── Logs ──────────────────────────────────────────────────────────────────────

async def get_logs(
    client: RemoteClient,
    service: str,
    query: str = "*",
    since: str = "30m",
    limit: int = 50,
    resource: str | None = None,
) -> list[LogEntry]:
    start, _ = parse_since(since)
    end = datetime.now(timezone.utc)
    return await client.query_logs(service, query, start, end, limit, resource_type=resource)


async def stream_logs(
    client: RemoteClient,
    service: str,
    query: str = "*",
    poll: float = 2.0,
    resource: str | None = None,
) -> AsyncGenerator[LogEntry, None]:
    async for entry in client.tail_logs(service, query, poll_interval=poll, resource_type=resource):
        yield entry


# ── Metrics ───────────────────────────────────────────────────────────────────

async def get_metrics(
    client: RemoteClient,
    service: str,
    metric: str,
    since: str = "1h",
    resource: str | None = None,
) -> list[MetricPoint]:
    start, _ = parse_since(since)
    end = datetime.now(timezone.utc)
    return await client.get_metrics(service, metric, start, end, resource_type=resource)


# ── Anomalies ─────────────────────────────────────────────────────────────────

async def get_anomalies(
    client: RemoteClient,
    service: str,
    since: str = "1h",
    severity: str | None = None,
    resource: str | None = None,
) -> list[Anomaly]:
    _, window = parse_since(since)
    anomalies = await client.detect_anomalies(service, window_minutes=window)
    if severity:
        anomalies = [a for a in anomalies if a.severity.lower() == severity.lower()]
    return anomalies


# ── Watches ───────────────────────────────────────────────────────────────────

async def start_watch(
    client: RemoteClient,
    service: str,
    notifier: str | None = None,
    destination: str | None = None,
    interval: int = 60,
) -> dict:
    return await client.create_watch(
        service=service,
        notifier=notifier,
        destination=destination,
        interval_seconds=interval,
    )


async def get_watches(client: RemoteClient) -> list[dict]:
    return await client.list_watches()


async def stop_watch(client: RemoteClient, watch_id: str) -> None:
    await client.stop_watch(watch_id)

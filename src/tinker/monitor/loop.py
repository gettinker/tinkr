"""Background monitoring loop: polls backends, detects anomalies, fires callbacks."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

import structlog

from tinker.backends.base import Anomaly, ObservabilityBackend
from tinker.config import settings

log = structlog.get_logger(__name__)

# Type alias for the alert callback
AlertCallback = Callable[[Anomaly], Awaitable[None]]


class MonitoringLoop:
    """Polls observability backends and fires alert callbacks on anomalies.

    Usage::

        loop = MonitoringLoop(backend=cloudwatch, services=["payments-api"])
        loop.add_alert_handler(my_slack_notifier)
        await loop.run()
    """

    def __init__(
        self,
        backend: ObservabilityBackend,
        services: list[str],
        poll_interval: int | None = None,
        cooldown_minutes: int | None = None,
    ) -> None:
        self._backend = backend
        self._services = services
        self._poll_interval = poll_interval or settings.poll_interval_seconds
        self._cooldown = timedelta(minutes=cooldown_minutes or settings.anomaly_cooldown_minutes)
        self._handlers: list[AlertCallback] = []
        self._last_alerted: dict[str, datetime] = {}  # service:metric → last alert time
        self._running = False

    def add_alert_handler(self, handler: AlertCallback) -> None:
        """Register an async callback that is called for each new anomaly."""
        self._handlers.append(handler)

    async def run(self) -> None:
        """Start the polling loop. Runs until cancelled."""
        self._running = True
        log.info(
            "monitor.start",
            services=self._services,
            interval=self._poll_interval,
        )
        while self._running:
            await self._tick()
            await asyncio.sleep(self._poll_interval)

    async def stop(self) -> None:
        self._running = False
        log.info("monitor.stop")

    async def _tick(self) -> None:
        """One poll cycle across all services."""
        for service in self._services:
            try:
                anomalies = await self._backend.detect_anomalies(service)
                for anomaly in anomalies:
                    if self._should_alert(anomaly):
                        await self._fire(anomaly)
            except Exception:
                log.exception("monitor.tick_error", service=service)

    def _should_alert(self, anomaly: Anomaly) -> bool:
        """Return False if we alerted for this service+metric recently (cooldown)."""
        key = f"{anomaly.service}:{anomaly.metric}"
        last = self._last_alerted.get(key)
        if last and datetime.now(timezone.utc) - last < self._cooldown:
            log.debug("monitor.cooldown_active", key=key)
            return False
        return True

    async def _fire(self, anomaly: Anomaly) -> None:
        key = f"{anomaly.service}:{anomaly.metric}"
        self._last_alerted[key] = datetime.now(timezone.utc)
        log.info(
            "monitor.anomaly_detected",
            service=anomaly.service,
            metric=anomaly.metric,
            severity=anomaly.severity,
        )
        for handler in self._handlers:
            try:
                await handler(anomaly)
            except Exception:
                log.exception("monitor.handler_error", handler=handler.__name__)

"""Tests for ObservabilityBackend base class utilities."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tinker.backends.base import LogEntry, MetricPoint, Anomaly, ObservabilityBackend


class _StubBackend(ObservabilityBackend):
    async def query_logs(self, service, query, start, end, limit=100):
        return []

    async def get_metrics(self, service, metric_name, start, end, dimensions=None):
        return []

    async def detect_anomalies(self, service, window_minutes=10):
        return []


class TestParseSince:
    def setup_method(self):
        self.backend = _StubBackend()

    def test_minutes(self):
        start = self.backend._parse_since("30m")
        delta = datetime.now(timezone.utc) - start
        assert 29 * 60 < delta.total_seconds() < 31 * 60

    def test_hours(self):
        start = self.backend._parse_since("2h")
        delta = datetime.now(timezone.utc) - start
        assert 119 * 60 < delta.total_seconds() < 121 * 60

    def test_days(self):
        start = self.backend._parse_since("1d")
        delta = datetime.now(timezone.utc) - start
        assert 23 * 3600 < delta.total_seconds() < 25 * 3600

    def test_invalid_unit(self):
        with pytest.raises(ValueError, match="Unknown time unit"):
            self.backend._parse_since("5x")


class TestLogEntry:
    def test_is_error_true(self):
        entry = LogEntry(
            timestamp=datetime.now(timezone.utc),
            message="boom",
            level="ERROR",
        )
        assert entry.is_error() is True

    def test_is_error_false(self):
        entry = LogEntry(
            timestamp=datetime.now(timezone.utc),
            message="ok",
            level="INFO",
        )
        assert entry.is_error() is False

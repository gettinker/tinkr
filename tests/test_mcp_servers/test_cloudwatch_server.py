"""Tests for the CloudWatch MCP server."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinker.backends.base import LogEntry, Anomaly


class TestCloudWatchMCPServer:

    @patch("tinker.mcp_servers.cloudwatch_server.CloudWatchBackend")
    def test_server_name(self, mock_backend_cls):
        from tinker.mcp_servers.cloudwatch_server import CloudWatchMCPServer
        server = CloudWatchMCPServer.__new__(CloudWatchMCPServer)
        assert server.server_name == "tinker-cloudwatch"

    @patch("tinker.mcp_servers.cloudwatch_server.CloudWatchBackend")
    @pytest.mark.asyncio
    async def test_handle_query_logs_returns_text_content(self, mock_backend_cls):
        from tinker.mcp_servers.cloudwatch_server import CloudWatchMCPServer

        mock_backend = MagicMock()
        mock_backend.query_logs = AsyncMock(return_value=[
            LogEntry(
                timestamp=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
                message="NullPointerException in PaymentHandler",
                level="ERROR",
            )
        ])
        mock_backend._parse_since = MagicMock(return_value=datetime(2024, 1, 1, tzinfo=timezone.utc))
        mock_backend_cls.return_value = mock_backend

        server = CloudWatchMCPServer()
        result = await server._handle_query_logs({"service": "payments", "query": "level:ERROR", "since": "1h"})

        assert len(result) == 1
        assert "NullPointerException" in result[0].text

    @patch("tinker.mcp_servers.cloudwatch_server.CloudWatchBackend")
    @pytest.mark.asyncio
    async def test_handle_detect_anomalies(self, mock_backend_cls):
        from tinker.mcp_servers.cloudwatch_server import CloudWatchMCPServer

        mock_backend = MagicMock()
        mock_backend.detect_anomalies = AsyncMock(return_value=[
            Anomaly(service="payments", metric="error_count", description="High errors", severity="high")
        ])
        mock_backend_cls.return_value = mock_backend

        server = CloudWatchMCPServer()
        result = await server._handle_detect_anomalies({"service": "payments"})

        assert len(result) == 1
        assert "high" in result[0].text

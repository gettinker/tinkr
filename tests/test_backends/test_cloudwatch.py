"""CloudWatch backend tests using moto."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest


class TestCloudWatchBackend:
    """Basic smoke tests — full integration tests require moto fixtures."""

    @patch("tinker.backends.cloudwatch.boto3.Session")
    def test_init(self, mock_session_cls):
        from tinker.backends.cloudwatch import CloudWatchBackend
        backend = CloudWatchBackend()
        assert backend is not None

    @patch("tinker.backends.cloudwatch.boto3.Session")
    @pytest.mark.asyncio
    async def test_query_logs_returns_list(self, mock_session_cls):
        from tinker.backends.cloudwatch import CloudWatchBackend

        mock_logs = MagicMock()
        mock_logs.start_query.return_value = {"queryId": "q1"}
        mock_logs.get_query_results.return_value = {"status": "Complete", "results": []}
        mock_session_cls.return_value.client.return_value = mock_logs

        backend = CloudWatchBackend()
        backend._logs = mock_logs

        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=1)
        result = await backend.query_logs("my-service", "level:ERROR", start, end)
        assert isinstance(result, list)

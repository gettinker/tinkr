"""GCP Cloud Logging and Cloud Monitoring backend."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog

from tinker.backends.base import Anomaly, LogEntry, MetricPoint, ObservabilityBackend
from tinker.config import settings

log = structlog.get_logger(__name__)


class GCPBackend(ObservabilityBackend):
    """Observability backend backed by GCP Cloud Logging and Cloud Monitoring."""

    def __init__(self) -> None:
        from google.cloud import logging as gcp_logging
        from google.cloud import monitoring_v3

        self._project = settings.gcp_project_id or ""
        self._logging_client = gcp_logging.Client(project=self._project)
        self._monitoring_client = monitoring_v3.MetricServiceClient()

    async def query_logs(
        self,
        service: str,
        query: str,
        start: datetime,
        end: datetime,
        limit: int = 100,
        resource_type: str | None = None,
    ) -> list[LogEntry]:
        import asyncio

        log.debug("gcp.query_logs", service=service)

        # Translate unified query to GCP filter; raw GCP filters (containing '=')
        # that look like native syntax are passed through unchanged.
        if "resource.labels" in query or query == "*":
            native_filter = (
                f'resource.labels.service_name="{service}" '
                f'AND ({query}) '
                f'AND timestamp>="{start.isoformat()}" '
                f'AND timestamp<="{end.isoformat()}"'
            )
        else:
            from tinker.query import parse_query, translate_for
            ast = parse_query(query)
            base = translate_for("gcp", ast, service=service, resource_type=resource_type)
            native_filter = (
                f'{base} '
                f'AND timestamp>="{start.isoformat()}" '
                f'AND timestamp<="{end.isoformat()}"'
            )
        filter_str = native_filter

        entries = await asyncio.to_thread(
            lambda: list(
                self._logging_client.list_entries(
                    filter_=filter_str,
                    page_size=limit,
                    order_by="timestamp desc",
                )
            )
        )

        return [self._parse_entry(e) for e in entries]

    def _parse_entry(self, entry: object) -> LogEntry:
        from google.cloud.logging import StructEntry, TextEntry

        ts = getattr(entry, "timestamp", datetime.now(timezone.utc))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        severity = str(getattr(entry, "severity", "INFO"))
        trace = str(getattr(entry, "trace", ""))

        if isinstance(entry, StructEntry):
            payload = entry.payload or {}
            message = payload.get("message", str(payload))
        elif isinstance(entry, TextEntry):
            message = entry.payload or ""
        else:
            message = str(getattr(entry, "payload", ""))

        return LogEntry(
            timestamp=ts,
            message=message,
            level=severity,
            trace_id=trace.split("/")[-1] if trace else "",
        )

    async def get_metrics(
        self,
        service: str,
        metric_name: str,
        start: datetime,
        end: datetime,
        dimensions: dict[str, str] | None = None,
        resource_type: str | None = None,
    ) -> list[MetricPoint]:
        import asyncio

        from google.cloud.monitoring_v3 import TimeInterval
        from google.protobuf.timestamp_pb2 import Timestamp

        log.debug("gcp.get_metrics", service=service, metric=metric_name)

        interval = TimeInterval(
            start_time=Timestamp(seconds=int(start.timestamp())),
            end_time=Timestamp(seconds=int(end.timestamp())),
        )
        project_name = f"projects/{self._project}"
        filter_str = (
            f'metric.type="custom.googleapis.com/{metric_name}" '
            f'AND resource.labels.service_name="{service}"'
        )

        results = await asyncio.to_thread(
            lambda: list(
                self._monitoring_client.list_time_series(
                    request={
                        "name": project_name,
                        "filter": filter_str,
                        "interval": interval,
                        "view": "FULL",
                    }
                )
            )
        )

        points: list[MetricPoint] = []
        for ts in results:
            for point in ts.points:
                pts = point.interval.start_time
                points.append(
                    MetricPoint(
                        timestamp=datetime.fromtimestamp(pts.seconds, tz=timezone.utc),
                        value=point.value.double_value,
                        unit=ts.metric_kind.name,
                    )
                )
        return points

    async def detect_anomalies(
        self,
        service: str,
        window_minutes: int = 10,
    ) -> list[Anomaly]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=window_minutes)
        anomalies: list[Anomaly] = []

        error_logs = await self.query_logs(
            service, 'severity="ERROR" OR severity="CRITICAL"', start, end, limit=200
        )
        if len(error_logs) > 10:
            representative, summary = self._summarize_logs(error_logs, window_minutes)
            anomalies.append(
                Anomaly(
                    service=service,
                    metric="error_count",
                    description=f"High error rate: {len(error_logs)} errors in {window_minutes}m",
                    severity="high",
                    current_value=float(len(error_logs)),
                    threshold=10.0,
                    recent_logs=representative,
                    log_summary=summary,
                )
            )

        return anomalies

"""AWS CloudWatch Logs and Metrics backend."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import boto3
import structlog

from tinker.backends.base import Anomaly, LogEntry, MetricPoint, ObservabilityBackend
from tinker.config import settings

log = structlog.get_logger(__name__)

# Default error rate threshold (percent) that triggers an anomaly
_DEFAULT_ERROR_RATE_THRESHOLD = 5.0


class CloudWatchBackend(ObservabilityBackend):
    """Observability backend backed by AWS CloudWatch Logs + Metrics."""

    def __init__(self) -> None:
        session = boto3.Session(
            profile_name=settings.aws_profile,
            region_name=settings.aws_region,
        )
        self._logs = session.client("logs")
        self._cw = session.client("cloudwatch")

    # ── Logs ──────────────────────────────────────────────────────────────────

    async def query_logs(
        self,
        service: str,
        query: str,
        start: datetime,
        end: datetime,
        limit: int = 100,
        resource_type: str | None = None,
    ) -> list[LogEntry]:
        """Run a CloudWatch Logs Insights query.

        `query` is a Tinker unified query string (e.g. 'level:ERROR AND "timeout"').
        `resource_type` controls which log group is targeted:
            lambda  → /aws/lambda/{service}
            ecs     → /ecs/{service}
            eks     → /aws/containerinsights/{service}/application
            None    → auto-discover via describe_log_groups
        Raw Insights queries (containing '|') are passed through unchanged.
        """
        from tinker.query import parse_query, translate_for
        from tinker.query.translators.cloudwatch import resolve_log_groups

        if "|" in query or query == "*":
            insights_query = query
        else:
            ast = parse_query(query)
            insights_query = translate_for("cloudwatch", ast, service=service)

        # Resolve which log group(s) to target
        log_groups: list[str] = resolve_log_groups(resource_type, service)
        if not log_groups:
            # Auto-discover: find all log groups whose name contains the service name
            log.debug("cloudwatch.auto_discover_log_groups", service=service)
            paginator = self._logs.get_paginator("describe_log_groups")
            log_groups = []
            for page in paginator.paginate(logGroupNamePattern=service):
                log_groups.extend(g["logGroupName"] for g in page.get("logGroups", []))
            if not log_groups:
                log.warning("cloudwatch.no_log_groups_found", service=service)
                return []

        log.debug("cloudwatch.query_logs", service=service, log_groups=log_groups)

        start_kwargs: dict[str, Any] = {
            "startTime": int(start.timestamp()),
            "endTime": int(end.timestamp()),
            "queryString": insights_query,
            "limit": limit,
        }
        if len(log_groups) == 1:
            start_kwargs["logGroupName"] = log_groups[0]
        else:
            start_kwargs["logGroupNames"] = log_groups

        response: dict[str, Any] = await asyncio.to_thread(
            self._logs.start_query,
            **start_kwargs,
        )
        query_id: str = response["queryId"]

        # Poll until complete
        while True:
            result = await asyncio.to_thread(self._logs.get_query_results, queryId=query_id)
            status: str = result["status"]
            if status in {"Complete", "Failed", "Cancelled"}:
                break
            await asyncio.sleep(0.5)

        if result["status"] != "Complete":
            log.warning("cloudwatch.query_failed", status=result["status"], query_id=query_id)
            return []

        return [self._parse_log_record(r) for r in result.get("results", [])]

    def _parse_log_record(self, record: list[dict[str, str]]) -> LogEntry:
        fields = {f["field"]: f["value"] for f in record}
        raw_ts = fields.get("@timestamp", "")
        try:
            ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except ValueError:
            ts = datetime.now(timezone.utc)

        return LogEntry(
            timestamp=ts,
            message=fields.get("@message", ""),
            level=fields.get("level", "INFO").upper(),
            service=fields.get("service", ""),
            trace_id=fields.get("traceId", ""),
            extra={k: v for k, v in fields.items() if not k.startswith("@")},
        )

    # ── Metrics ───────────────────────────────────────────────────────────────

    async def get_metrics(
        self,
        service: str,
        metric_name: str,
        start: datetime,
        end: datetime,
        dimensions: dict[str, str] | None = None,
        resource_type: str | None = None,
    ) -> list[MetricPoint]:
        dims = [{"Name": k, "Value": v} for k, v in (dimensions or {}).items()]
        log.debug("cloudwatch.get_metrics", service=service, metric=metric_name)

        response = await asyncio.to_thread(
            self._cw.get_metric_data,
            MetricDataQueries=[
                {
                    "Id": "m1",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": f"AWS/{service}",
                            "MetricName": metric_name,
                            "Dimensions": dims,
                        },
                        "Period": 60,
                        "Stat": "Average",
                    },
                }
            ],
            StartTime=start,
            EndTime=end,
        )

        results = response["MetricDataResults"][0]
        return [
            MetricPoint(timestamp=ts, value=val, unit=results.get("Label", ""))
            for ts, val in zip(results["Timestamps"], results["Values"])
        ]

    # ── Anomaly detection ─────────────────────────────────────────────────────

    async def detect_anomalies(
        self,
        service: str,
        window_minutes: int = 10,
    ) -> list[Anomaly]:
        from datetime import timedelta

        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=window_minutes)
        anomalies: list[Anomaly] = []

        # Check error rate via Insights
        error_query = (
            "fields @timestamp, @message, level "
            "| filter level in ['ERROR', 'CRITICAL'] "
            "| stats count() as errors by bin(1m)"
        )
        try:
            error_logs = await self.query_logs(service, error_query, start, end, limit=200)
            if len(error_logs) > 10:  # crude threshold; replace with dynamic baseline
                anomalies.append(
                    Anomaly(
                        service=service,
                        metric="error_count",
                        description=f"High error rate: {len(error_logs)} errors in {window_minutes}m",
                        severity="high",
                        current_value=float(len(error_logs)),
                        threshold=10.0,
                        recent_logs=error_logs[:20],
                    )
                )
        except Exception:
            log.exception("cloudwatch.detect_anomalies.error_check_failed", service=service)

        return anomalies

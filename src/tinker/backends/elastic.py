"""Elasticsearch / OpenSearch backend."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from tinker.backends.base import Anomaly, LogEntry, MetricPoint, ObservabilityBackend
from tinker.config import settings

log = structlog.get_logger(__name__)


class ElasticBackend(ObservabilityBackend):
    """Observability backend backed by Elasticsearch or OpenSearch."""

    def __init__(self) -> None:
        from elasticsearch import AsyncElasticsearch

        api_key = settings.elasticsearch_api_key
        self._client = AsyncElasticsearch(
            hosts=[settings.elasticsearch_url or "http://localhost:9200"],
            api_key=api_key.get_secret_value() if api_key else None,
        )
        self._index_pattern = "logs-*"

    async def query_logs(
        self,
        service: str,
        query: str,
        start: datetime,
        end: datetime,
        limit: int = 100,
        resource_type: str | None = None,
    ) -> list[LogEntry]:
        log.debug("elastic.query_logs", service=service)

        from tinker.query import parse_query, translate_for
        from tinker.query.translators.elastic import resolve_index

        ast = parse_query(query)
        query_dsl = translate_for("elastic", ast, service=service, resource_type=resource_type)
        index = resolve_index(resource_type)

        body: dict[str, Any] = {
            "size": limit,
            "sort": [{"@timestamp": {"order": "desc"}}],
            "query": {
                "bool": {
                    "must": [query_dsl],
                    "filter": [
                        {
                            "range": {
                                "@timestamp": {
                                    "gte": start.isoformat(),
                                    "lte": end.isoformat(),
                                }
                            }
                        }
                    ],
                }
            },
        }

        log.debug("elastic.query_logs", service=service, index=index)
        response = await self._client.search(index=index, body=body)
        hits = response["hits"]["hits"]
        return [self._parse_hit(h) for h in hits]

    def _parse_hit(self, hit: dict[str, Any]) -> LogEntry:
        src = hit.get("_source", {})
        raw_ts = src.get("@timestamp", "")
        try:
            ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except ValueError:
            ts = datetime.now(timezone.utc)

        return LogEntry(
            timestamp=ts,
            message=src.get("message", ""),
            level=src.get("log", {}).get("level", "INFO").upper(),
            service=src.get("service", {}).get("name", ""),
            trace_id=src.get("trace", {}).get("id", ""),
            span_id=src.get("span", {}).get("id", ""),
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
        """Use a date_histogram aggregation to compute a metric over time."""
        log.debug("elastic.get_metrics", service=service, metric=metric_name)

        body: dict[str, Any] = {
            "size": 0,
            "query": {
                "bool": {
                    "must": [{"term": {"service.name": service}}],
                    "filter": [
                        {
                            "range": {
                                "@timestamp": {
                                    "gte": start.isoformat(),
                                    "lte": end.isoformat(),
                                }
                            }
                        }
                    ],
                }
            },
            "aggs": {
                "over_time": {
                    "date_histogram": {
                        "field": "@timestamp",
                        "fixed_interval": "1m",
                    },
                    "aggs": {
                        "metric_value": {"avg": {"field": metric_name}},
                    },
                }
            },
        }

        response = await self._client.search(index=self._index_pattern, body=body)
        buckets = response["aggregations"]["over_time"]["buckets"]
        return [
            MetricPoint(
                timestamp=datetime.fromtimestamp(b["key"] / 1000, tz=timezone.utc),
                value=b["metric_value"].get("value") or 0.0,
            )
            for b in buckets
        ]

    async def detect_anomalies(
        self,
        service: str,
        window_minutes: int = 10,
    ) -> list[Anomaly]:
        from datetime import timedelta

        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=window_minutes)
        anomalies: list[Anomaly] = []

        error_logs = await self.query_logs(
            service, 'log.level:(ERROR OR CRITICAL)', start, end, limit=200
        )
        if len(error_logs) > 10:
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

        return anomalies

    async def close(self) -> None:
        await self._client.close()

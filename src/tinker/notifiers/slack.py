"""Slack alert notifier."""

from __future__ import annotations

from typing import Any

import structlog

from tinker.notifiers.base import AlertNotifier

log = structlog.get_logger(__name__)


class SlackNotifier(AlertNotifier):
    """Sends alerts to a Slack channel via the Web API."""

    type_name = "slack"

    def __init__(self, token: str, default_channel: str = "#incidents") -> None:
        self._token = token
        self._default_channel = default_channel

    async def send_alert(
        self,
        anomalies: list[Any],
        service: str,
        destination: str | None,
        watch_id: str,
    ) -> None:
        ch = destination or self._default_channel
        try:
            from slack_sdk.web.async_client import AsyncWebClient
            client = AsyncWebClient(token=self._token)
            lines = [f"*Tinker Watch* — `{service}`  [{watch_id}]", ""]
            for a in anomalies[:5]:
                lines.append(f"• *{a.severity.upper()}* `{a.metric}` — {a.description}")
            if len(anomalies) > 5:
                lines.append(f"_…and {len(anomalies) - 5} more_")
            await client.chat_postMessage(channel=ch, text="\n".join(lines))
            log.info("notifier.slack.sent", watch_id=watch_id, channel=ch, count=len(anomalies))
        except Exception as exc:
            err_str = str(exc)
            if "not_in_channel" in err_str:
                log.warning(
                    "notifier.slack.not_in_channel",
                    watch_id=watch_id,
                    channel=ch,
                    hint=f"Invite the bot to {ch} with /invite @<bot-name>",
                    error=err_str,
                )
            else:
                log.warning("notifier.slack.failed", watch_id=watch_id, channel=ch, error=err_str)
            raise

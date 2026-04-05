"""Alert notifier interface and built-in implementations.

The ``AlertNotifier`` ABC decouples WatchManager from any specific messaging
platform.  Each notifier is registered under a name in ``NotifierRegistry``
and configured via ``[notifiers.<name>]`` in ``~/.tinker/config.toml``.

Example config
--------------
    [notifiers.default]
    type = "slack"
    bot_token = "env:SLACK_BOT_TOKEN"
    channel = "#incidents"

    [notifiers.ops-discord]
    type = "discord"
    webhook_url = "env:DISCORD_WEBHOOK_URL"

    [notifiers.pagerduty]
    type = "webhook"
    url = "env:PAGERDUTY_WEBHOOK_URL"
    header_Authorization = "env:PAGERDUTY_API_KEY"

Watch creation
--------------
POST /api/v1/watches
{
  "service": "payments-api",
  "notifier": "default",          # name from [notifiers.*]
  "destination": "#payments-ops"  # optional override; meaning is notifier-specific
}

If ``notifier`` is omitted the registry uses "default".
If ``destination`` is omitted the notifier uses its own configured default.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from tinker.toml_config import NotifierConfig

log = structlog.get_logger(__name__)


# ── ABC ───────────────────────────────────────────────────────────────────────

class AlertNotifier(ABC):
    """Send an alert for a set of anomalies to a destination."""

    @property
    @abstractmethod
    def type_name(self) -> str:
        """Short type identifier, e.g. 'slack', 'discord', 'webhook'."""

    @abstractmethod
    async def send_alert(
        self,
        anomalies: list[Any],   # list[Anomaly] — avoid circular import
        service: str,
        destination: str | None,
        watch_id: str,
    ) -> None:
        """Send anomaly alert.

        Parameters
        ----------
        anomalies:   non-empty list of Anomaly objects
        service:     service name, for display
        destination: platform-specific target (Slack channel, webhook URL …).
                     None means use the notifier's own configured default.
        watch_id:    watch identifier, for correlation
        """


# ── Slack ─────────────────────────────────────────────────────────────────────

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
            log.warning("notifier.slack.failed", watch_id=watch_id, channel=ch, error=str(exc))
            raise


# ── Discord ───────────────────────────────────────────────────────────────────

class DiscordNotifier(AlertNotifier):
    """Sends alerts to a Discord channel via an Incoming Webhook.

    ``destination`` is ignored; the webhook URL is fixed at config time.
    """

    type_name = "discord"

    def __init__(self, webhook_url: str) -> None:
        self._url = webhook_url

    async def send_alert(
        self,
        anomalies: list[Any],
        service: str,
        destination: str | None,
        watch_id: str,
    ) -> None:
        import httpx

        lines = [f"**Tinker Watch** — `{service}`  [{watch_id}]", ""]
        for a in anomalies[:5]:
            lines.append(f"• **{a.severity.upper()}** `{a.metric}` — {a.description}")
        if len(anomalies) > 5:
            lines.append(f"*…and {len(anomalies) - 5} more*")

        payload = {"content": "\n".join(lines)}
        async with httpx.AsyncClient() as client:
            resp = await client.post(self._url, json=payload, timeout=10)
            resp.raise_for_status()
        log.info("notifier.discord.sent", watch_id=watch_id, count=len(anomalies))


# ── Generic webhook ───────────────────────────────────────────────────────────

class WebhookNotifier(AlertNotifier):
    """Posts a JSON payload to an HTTP endpoint (PagerDuty, custom receiver, etc.).

    ``destination`` overrides the configured URL if provided.

    Headers prefixed with ``header_`` in config options are sent with the request,
    e.g. ``header_Authorization = "Bearer …"``.
    """

    type_name = "webhook"

    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self._url = url
        self._headers = headers or {}

    async def send_alert(
        self,
        anomalies: list[Any],
        service: str,
        destination: str | None,
        watch_id: str,
    ) -> None:
        import httpx

        url = destination or self._url
        payload: dict[str, Any] = {
            "watch_id": watch_id,
            "service": service,
            "anomaly_count": len(anomalies),
            "anomalies": [a.to_dict() for a in anomalies],
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, headers=self._headers, timeout=10)
            resp.raise_for_status()
        log.info("notifier.webhook.sent", watch_id=watch_id, url=url, count=len(anomalies))


# ── Registry ──────────────────────────────────────────────────────────────────

class NotifierRegistry:
    """Maps notifier names to ``AlertNotifier`` instances.

    Populated at server startup from ``[notifiers.*]`` in config.toml.
    WatchManager calls ``registry.send(name, ...)`` without knowing the platform.
    """

    def __init__(self) -> None:
        self._notifiers: dict[str, AlertNotifier] = {}

    def register(self, name: str, notifier: AlertNotifier) -> None:
        self._notifiers[name] = notifier

    def get(self, name: str) -> AlertNotifier | None:
        return self._notifiers.get(name)

    def __len__(self) -> int:
        return len(self._notifiers)

    async def send(
        self,
        notifier_name: str | None,
        anomalies: list[Any],
        service: str,
        destination: str | None,
        watch_id: str,
    ) -> None:
        """Dispatch to the named notifier.

        Falls back to "default" if *notifier_name* is None.
        Falls back to the first registered notifier if "default" is not registered.
        """
        name = notifier_name or "default"
        notifier = self._notifiers.get(name)
        if notifier is None and self._notifiers:
            # Accept any single registered notifier when name is missing
            if len(self._notifiers) == 1:
                notifier = next(iter(self._notifiers.values()))
            else:
                log.warning("notifier.not_found", name=name, registered=list(self._notifiers))
                return
        if notifier is None:
            log.warning("notifier.registry_empty", watch_id=watch_id)
            return
        try:
            await notifier.send_alert(anomalies, service, destination, watch_id)
        except Exception as exc:
            log.warning("notifier.send_failed", notifier=name, watch_id=watch_id, error=str(exc))

    def build_from_toml(self, notifiers_cfg: dict[str, NotifierConfig]) -> None:
        """Register all notifiers defined in config.toml."""
        for name, cfg in notifiers_cfg.items():
            notifier = _make_notifier(cfg.type, cfg.options)
            if notifier is not None:
                self.register(name, notifier)
                log.info("notifier.registered", name=name, type=cfg.type)


# ── Factory ───────────────────────────────────────────────────────────────────

def _make_notifier(type_key: str, options: dict[str, str]) -> AlertNotifier | None:
    if type_key == "slack":
        token = options.get("bot_token") or options.get("token")
        if not token:
            log.warning("notifier.slack.no_token")
            return None
        channel = options.get("channel", "#incidents")
        return SlackNotifier(token=token, default_channel=channel)

    if type_key == "discord":
        url = options.get("webhook_url", "")
        if not url:
            log.warning("notifier.discord.no_webhook_url")
            return None
        return DiscordNotifier(webhook_url=url)

    if type_key == "webhook":
        url = options.get("url", "")
        if not url:
            log.warning("notifier.webhook.no_url")
            return None
        # Collect header_* keys as HTTP headers
        headers = {
            k[len("header_"):]: v
            for k, v in options.items()
            if k.startswith("header_")
        }
        return WebhookNotifier(url=url, headers=headers)

    log.warning("notifier.unknown_type", type=type_key)
    return None

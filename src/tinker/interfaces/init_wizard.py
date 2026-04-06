"""tinker init wizards.

Two wizards:
  ServerWizard — run on the machine that will host tinker server
                 auto-detects cloud, checks IAM/permissions, configures Slack, writes .env
  CLIWizard    — run on a developer's laptop
                 asks for server URL + API token, tests connection, writes ~/.tinker/config
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sys
from pathlib import Path

import structlog
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax

log = structlog.get_logger(__name__)
console = Console()


# ── Cloud / backend catalogues ────────────────────────────────────────────────

CLOUD_CHOICES = [
    ("AWS (CloudWatch + X-Ray)",                 "cloudwatch",  "aws"),
    ("GCP (Cloud Logging + Monitoring)",         "gcp",         "gcp"),
    ("Azure (Log Analytics + Monitor)",          "azure",       "azure"),
    ("Self-hosted (Grafana + Prometheus + Loki)", "grafana",     "grafana"),
    ("Datadog",                                  "datadog",     "datadog"),
    ("Elastic / OpenSearch",                     "elastic",     "elastic"),
]

LLM_CHOICES = [
    ("Anthropic (Claude) — direct",      "anthropic",  "ANTHROPIC_API_KEY"),
    ("OpenRouter — access 100+ models",  "openrouter", "OPENROUTER_API_KEY"),
    ("OpenAI (GPT-4o etc.)",             "openai",     "OPENAI_API_KEY"),
    ("Groq — fast open-source models",   "groq",       "GROQ_API_KEY"),
]

# (label, model_id)  — first entry is the default
LLM_MODEL_CHOICES: dict[str, list[tuple[str, str]]] = {
    "anthropic": [
        ("claude-sonnet-4-6  (recommended — fast + smart)", "anthropic/claude-sonnet-4-6"),
        ("claude-opus-4-6    (most capable, slower)",       "anthropic/claude-opus-4-6"),
        ("claude-haiku-4-5   (cheapest, fastest)",          "anthropic/claude-haiku-4-5-20251001"),
    ],
    "openrouter": [
        ("claude-sonnet-4-6  (recommended)", "openrouter/anthropic/claude-sonnet-4-6"),
        ("claude-opus-4-6",                  "openrouter/anthropic/claude-opus-4-6"),
        ("gpt-4o",                           "openrouter/openai/gpt-4o"),
        ("gpt-4o-mini        (cheaper)",     "openrouter/openai/gpt-4o-mini"),
        ("llama-3.1-70b      (free tier)",   "openrouter/meta-llama/llama-3.1-70b-instruct"),
        ("gemini-pro-1.5",                   "openrouter/google/gemini-pro-1.5"),
    ],
    "openai": [
        ("gpt-4o             (recommended)", "openai/gpt-4o"),
        ("gpt-4o-mini        (cheaper)",     "openai/gpt-4o-mini"),
        ("o1-preview         (reasoning)",   "openai/o1-preview"),
    ],
    "groq": [
        ("llama-3.1-70b-versatile  (recommended)", "groq/llama-3.1-70b-versatile"),
        ("llama-3.1-8b-instant     (fastest)",     "groq/llama-3.1-8b-instant"),
        ("mixtral-8x7b-32768",                     "groq/mixtral-8x7b-32768"),
    ],
}

# Deep RCA model defaults per provider (used when --deep flag is set)
_DEEP_MODEL_DEFAULTS: dict[str, str] = {
    "anthropic":  "anthropic/claude-opus-4-6",
    "openrouter": "openrouter/anthropic/claude-opus-4-6",
    "openai":     "openai/o1-preview",
    "groq":       "groq/llama-3.1-70b-versatile",
}

# ── IAM permission guides ─────────────────────────────────────────────────────

AWS_POLICY = """\
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow",
      "Action": ["logs:StartQuery","logs:GetQueryResults","logs:DescribeLogGroups",
                 "logs:FilterLogEvents","logs:GetLogEvents"],
      "Resource": "*" },
    { "Effect": "Allow",
      "Action": ["cloudwatch:GetMetricData","cloudwatch:ListMetrics","cloudwatch:DescribeAlarms"],
      "Resource": "*" },
    { "Effect": "Allow",
      "Action": ["xray:GetTraceSummaries","xray:BatchGetTraces"],
      "Resource": "*" }
  ]
}"""

GCP_IAM_COMMANDS = """\
gcloud projects add-iam-policy-binding PROJECT_ID \\
  --member="serviceAccount:tinker@PROJECT_ID.iam.gserviceaccount.com" \\
  --role="roles/logging.viewer"
gcloud projects add-iam-policy-binding PROJECT_ID \\
  --member="serviceAccount:tinker@PROJECT_ID.iam.gserviceaccount.com" \\
  --role="roles/monitoring.viewer"\
"""

AZURE_IAM_COMMANDS = """\
az role assignment create --assignee <MANAGED_IDENTITY_PRINCIPAL_ID> \\
  --role "Monitoring Reader" --scope /subscriptions/SUBSCRIPTION_ID
az role assignment create --assignee <MANAGED_IDENTITY_PRINCIPAL_ID> \\
  --role "Log Analytics Reader" --scope /subscriptions/SUBSCRIPTION_ID\
"""


# ══════════════════════════════════════════════════════════════════════════════
# ServerWizard
# ══════════════════════════════════════════════════════════════════════════════

class ServerWizard:
    """Wizard for setting up and running tinker server on this machine."""

    def __init__(self) -> None:
        self.env_file = Path.home() / ".tinker" / ".env"
        self.toml_file = Path.home() / ".tinker" / "config.toml"
        self._env: dict[str, str] = {}       # secrets → written to .env
        self._toml: dict[str, object] = {}   # structure → written to config.toml
        self._slack_configured = False       # set by _step_slack for _step_notifiers
        self._slack_channel = "#incidents"   # carries the channel picked in _step_slack

    def run(self) -> None:
        """Full first-time setup wizard."""
        console.print(Panel.fit(
            "[bold cyan]Tinker Server Setup[/bold cyan]\n"
            "This wizard configures the server that runs in your cloud environment.\n\n"
            "[dim]Press Ctrl+C at any time to exit.[/dim]",
            border_style="cyan",
        ))
        console.print()

        try:
            self._step_llm()
            self._step_slack()
            self._step_github()
            self._step_api_key()
            self._step_profiles()
            self._write_env()
            self._write_toml()
            self._show_next_steps()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Setup cancelled.[/yellow]")
            sys.exit(0)

    def run_add_profile(self) -> None:
        """Add a single profile to an existing config (used by 'tinker profile add')."""
        # Load existing config into self._toml so we can append and re-write
        try:
            import tomllib as _tomllib  # Python 3.11+
        except ImportError:
            try:
                import tomli as _tomllib  # type: ignore[no-redef]
            except ImportError:
                console.print("[red]Install tomli or use Python 3.11+ to modify config.toml.[/red]")
                sys.exit(1)

        if self.toml_file.exists():
            raw = _tomllib.loads(self.toml_file.read_text(encoding="utf-8"))
            # Reconstruct _toml dict from existing file
            for key in ("llm", "slack", "github", "auth", "server"):
                if key in raw:
                    self._toml[key] = raw[key]
            self._toml["profiles"] = raw.get("profiles", {})
            if "active_profile" in raw:
                self._toml["active_profile"] = raw["active_profile"]
            # Load any existing env vars so secrets aren't lost
            if self.env_file.exists():
                for line in self.env_file.read_text().splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        self._env[k.strip()] = v.strip().strip("'\"")

        try:
            self._step_one_profile()
            self._write_env()
            self._write_toml()
            console.print("[green]✓ Profile added.[/green]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Cancelled.[/yellow]")
            sys.exit(0)

    # ── Steps ─────────────────────────────────────────────────────────────────

    def _step_profiles(self) -> None:
        console.print()
        console.print(Rule("[bold]Step 5 — Profiles (Cloud / Observability Backends)[/bold]"))
        console.print()
        console.print(
            "[dim]A profile bundles a cloud backend with its services and alert notifiers.\n"
            "Configure one profile per cloud account. You can add more profiles later\n"
            "with [bold]tinker profile add[/bold].[/dim]"
        )
        self._step_one_profile(first=True)
        while _ask_yes_no("\nAdd another profile?", default=False):
            self._step_one_profile(first=False)

    def _step_one_profile(self, first: bool = True) -> None:
        """Collect config for a single profile and append it to self._toml['profiles']."""
        console.print()
        profiles: dict = self._toml.setdefault("profiles", {})  # type: ignore[assignment]

        # ── Profile name ──────────────────────────────────────────────────────
        default_name = "default" if first and not profiles else ""
        hint = f" [{default_name}]" if default_name else ""
        while True:
            name = input(f"Profile name{hint}: ").strip() or default_name
            if not name:
                console.print("[red]Name required.[/red]")
                continue
            if name in profiles:
                console.print(f"[yellow]Profile '{name}' already exists — choose a different name.[/yellow]")
                continue
            break

        # ── Backend ───────────────────────────────────────────────────────────
        console.print()
        detected = _detect_cloud()
        if detected and first:
            console.print(f"[green]Auto-detected:[/green] [bold]{detected['label']}[/bold]")
            if _ask_yes_no(f"Use {detected['label']} for profile '{name}'?", default=True):
                backend_opts = self._collect_cloud(detected["cloud"])
                backend_opts["backend"] = detected["backend"]
            else:
                backend_opts = self._pick_cloud()
        else:
            backend_opts = self._pick_cloud()

        # ── Notifiers ─────────────────────────────────────────────────────────
        notifiers = self._collect_notifiers_for_profile(name)

        # ── Services ──────────────────────────────────────────────────────────
        services = self._collect_services_for_profile()

        # Assemble the profile dict (backend key + options + nested tables)
        profile: dict = {k: v for k, v in backend_opts.items()}
        if notifiers:
            profile["notifiers"] = notifiers
        if services:
            profile["services"] = services
        profiles[name] = profile

        # First profile becomes the active one if none set yet
        if "active_profile" not in self._toml:
            self._toml["active_profile"] = name
            console.print(f"[green]✓[/green] Profile [bold]{name}[/bold] created and set as active.")
        else:
            console.print(f"[green]✓[/green] Profile [bold]{name}[/bold] created.")

    # ── Backend collectors (return a dict, no side-effects on self._toml) ─────

    def _pick_cloud(self) -> dict:
        console.print()
        for i, (label, _, _) in enumerate(CLOUD_CHOICES, 1):
            console.print(f"  [{i}] {label}")
        console.print()
        while True:
            raw = input("Select cloud [1]: ").strip() or "1"
            try:
                idx = int(raw) - 1
                _, backend, cloud = CLOUD_CHOICES[idx]
                break
            except (ValueError, IndexError):
                console.print("[red]Invalid choice.[/red]")
        opts = self._collect_cloud(cloud)
        opts["backend"] = backend
        return opts

    def _collect_cloud(self, cloud: str) -> dict:
        """Prompt for cloud-specific settings. Returns options dict (no 'backend' key)."""
        console.print()
        if cloud == "aws":
            return self._collect_aws()
        elif cloud == "gcp":
            return self._collect_gcp()
        elif cloud == "azure":
            return self._collect_azure()
        elif cloud == "grafana":
            return self._collect_grafana()
        elif cloud == "datadog":
            return self._collect_datadog()
        elif cloud == "elastic":
            return self._collect_elastic()
        return {}

    def _collect_aws(self) -> dict:
        console.print("[dim]Checking AWS CloudWatch permissions...[/dim]")
        try:
            import boto3
            boto3.client("logs").describe_log_groups(limit=1)
            console.print("[green]✓ CloudWatch Logs read access confirmed.[/green]")
        except Exception as exc:
            msg = str(exc)
            if "credential" in msg.lower() or "NoCredentials" in str(type(exc)):
                console.print("[yellow]✗ No AWS credentials found.[/yellow]")
                console.print("[dim]Attach an IAM role to this instance with the following policy:[/dim]")
                console.print(Syntax(AWS_POLICY, "json", theme="monokai"))
            else:
                console.print(f"[yellow]! CloudWatch check: {msg[:80]}[/yellow]")
        region = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or ""
        if not region:
            region = input("AWS region [us-east-1]: ").strip() or "us-east-1"
        return {"region": region}

    def _collect_gcp(self) -> dict:
        console.print("[dim]Checking GCP Cloud Logging permissions...[/dim]")
        try:
            from google.cloud import logging as gcp_logging
            gcp_logging.Client().list_entries(max_results=1)
            console.print("[green]✓ Cloud Logging read access confirmed.[/green]")
        except Exception as exc:
            console.print(f"[yellow]! GCP check: {str(exc)[:80]}[/yellow]")
            console.print("[dim]Required IAM roles:[/dim]")
            console.print(Syntax(GCP_IAM_COMMANDS, "bash", theme="monokai"))
        project = os.environ.get("GOOGLE_CLOUD_PROJECT") or ""
        if not project:
            project = input("GCP project ID: ").strip()
        opts: dict[str, str] = {}
        if project:
            opts["project_id"] = project
        return opts

    def _collect_azure(self) -> dict:
        console.print("[dim]Checking Azure Monitor permissions...[/dim]")
        workspace_id = os.environ.get("AZURE_WORKSPACE_ID") or ""
        if not workspace_id:
            workspace_id = input("Log Analytics workspace ID: ").strip()
        subscription_id = input("Azure subscription ID (optional): ").strip()
        resource_group = input("Azure resource group (optional): ").strip()
        opts: dict[str, str] = {}
        if workspace_id:
            opts["workspace_id"] = workspace_id
        if subscription_id:
            opts["subscription_id"] = subscription_id
        if resource_group:
            opts["resource_group"] = resource_group
        try:
            from azure.identity import DefaultAzureCredential
            from azure.monitor.query import LogsQueryClient
            from datetime import timedelta
            qc = LogsQueryClient(DefaultAzureCredential())
            qc.query_workspace(workspace_id, "AzureDiagnostics | take 1", timespan=timedelta(minutes=5))
            console.print("[green]✓ Log Analytics read access confirmed.[/green]")
        except Exception as exc:
            console.print(f"[yellow]! Azure check: {str(exc)[:80]}[/yellow]")
            console.print("[dim]Required role assignments:[/dim]")
            console.print(Syntax(AZURE_IAM_COMMANDS, "bash", theme="monokai"))
        return opts

    def _collect_grafana(self) -> dict:
        loki = input("Loki URL [http://localhost:3100]: ").strip() or "http://localhost:3100"
        prom = input("Prometheus URL [http://localhost:9090]: ").strip() or "http://localhost:9090"
        tempo = input("Tempo URL (optional): ").strip()
        api_key = input("Grafana API key (leave blank for no auth): ").strip()
        user = input("Basic auth user (leave blank if not used): ").strip()
        password = input("Basic auth password (leave blank if not used): ").strip() if user else ""
        console.print()
        console.print("[dim]Loki service label — the stream selector label your log shipper sets for the service name.[/dim]")
        console.print("[dim]Common values: service (Promtail default), app (Helm charts), job, service_name, container[/dim]")
        svc_label = input("Service label [service]: ").strip() or "service"
        opts: dict[str, str] = {"loki_url": loki, "prometheus_url": prom, "service_label": svc_label}
        if tempo:
            opts["tempo_url"] = tempo
        if api_key:
            self._env["GRAFANA_API_KEY"] = api_key
            opts["api_key"] = "env:GRAFANA_API_KEY"
        elif user and password:
            self._env["GRAFANA_USER"] = user
            self._env["GRAFANA_PASSWORD"] = password
            opts["user"] = "env:GRAFANA_USER"
            opts["password"] = "env:GRAFANA_PASSWORD"
        return opts

    def _collect_datadog(self) -> dict:
        api_key = input("Datadog API key: ").strip()
        app_key = input("Datadog App key: ").strip()
        site = input("Datadog site [datadoghq.com]: ").strip() or "datadoghq.com"
        if api_key:
            self._env["DATADOG_API_KEY"] = api_key
        if app_key:
            self._env["DATADOG_APP_KEY"] = app_key
        return {
            "site": site,
            "api_key": "env:DATADOG_API_KEY",
            "app_key": "env:DATADOG_APP_KEY",
        }

    def _collect_elastic(self) -> dict:
        url = input("Elasticsearch URL [http://localhost:9200]: ").strip() or "http://localhost:9200"
        api_key = input("Elasticsearch API key (leave blank for no auth): ").strip()
        opts: dict[str, str] = {"url": url}
        if api_key:
            self._env["ELASTICSEARCH_API_KEY"] = api_key
            opts["api_key"] = "env:ELASTICSEARCH_API_KEY"
        return opts

    def _step_llm(self) -> None:
        console.print()
        console.print(Rule("[bold]Step 1 — LLM Provider & Model[/bold]"))

        # ── Provider ──────────────────────────────────────────────────────────
        console.print()
        for i, (label, _, _) in enumerate(LLM_CHOICES, 1):
            console.print(f"  [{i}] {label}")
        console.print()

        while True:
            raw = input("Select LLM provider [1]: ").strip() or "1"
            try:
                idx = int(raw) - 1
                label, provider, key_name = LLM_CHOICES[idx]
                break
            except (ValueError, IndexError):
                console.print("[red]Invalid choice.[/red]")

        api_key = input(f"{key_name}: ").strip()
        if api_key:
            self._env[key_name] = api_key

        # ── Model selection ───────────────────────────────────────────────────
        models = LLM_MODEL_CHOICES.get(provider, [])
        console.print()
        console.print("[dim]Select default model (used for triage and anomaly explain):[/dim]")
        for i, (mlabel, _) in enumerate(models, 1):
            console.print(f"  [{i}] {mlabel}")
        console.print()

        while True:
            raw = input("Select model [1]: ").strip() or "1"
            try:
                midx = int(raw) - 1
                _, default_model = models[midx]
                break
            except (ValueError, IndexError):
                console.print("[red]Invalid choice.[/red]")

        # Deep RCA model — default to opus/most capable, let user override
        deep_default = _DEEP_MODEL_DEFAULTS.get(provider, default_model)
        console.print()
        console.print(f"[dim]Deep RCA model (used with --deep flag, defaults to most capable):[/dim]")
        for i, (mlabel, _) in enumerate(models, 1):
            marker = " ← default" if models[i - 1][1] == deep_default else ""
            console.print(f"  [{i}] {mlabel}{marker}")
        console.print()

        while True:
            deep_default_idx = next(
                (i for i, (_, mid) in enumerate(models, 1) if mid == deep_default), 1
            )
            raw = input(f"Select deep RCA model [{deep_default_idx}]: ").strip() or str(deep_default_idx)
            try:
                didx = int(raw) - 1
                _, deep_model = models[didx]
                break
            except (ValueError, IndexError):
                console.print("[red]Invalid choice.[/red]")

        self._toml["llm"] = {"default_model": default_model, "deep_rca_model": deep_model}
        console.print(f"[green]✓[/green] Default model:  [dim]{default_model}[/dim]")
        console.print(f"[green]✓[/green] Deep RCA model: [dim]{deep_model}[/dim]")

    def _step_slack(self) -> None:
        console.print()
        console.print(Rule("[bold]Step 2 — Slack (optional)[/bold]"))
        console.print()

        if not _ask_yes_no("Set up Slack alerts?", default=False):
            return

        bot_token = input("Slack bot token (xoxb-...): ").strip()
        if not bot_token:
            return
        self._env["SLACK_BOT_TOKEN"] = bot_token

        channel = input("Default alert channel [#incidents]: ").strip() or "#incidents"
        # Token is a secret → .env; channel is structure → TOML
        self._toml["slack"] = {
            "bot_token": "env:SLACK_BOT_TOKEN",
            "alerts_channel": channel,
        }
        self._slack_configured = True
        self._slack_channel = channel

        # Test it
        console.print("[dim]Testing Slack connection...[/dim]")
        try:
            import asyncio
            from slack_sdk.web.async_client import AsyncWebClient
            client = AsyncWebClient(token=bot_token)
            asyncio.run(client.auth_test())
            console.print("[green]✓ Slack connection confirmed.[/green]")
        except Exception as exc:
            console.print(f"[yellow]! Slack test failed: {str(exc)[:60]}[/yellow]")

    def _collect_notifiers_for_profile(self, profile_name: str) -> dict:
        """Collect notifiers for one profile. Returns a notifiers dict."""
        console.print()
        console.print(f"[dim]Notifiers for profile [bold]{profile_name}[/bold] — "
                      "deliver watch alerts to Slack, Discord, or webhooks.[/dim]")
        console.print()

        notifiers: dict[str, dict] = {}

        # Auto-create default Slack notifier if Slack was configured globally
        if self._slack_configured:
            notifiers["default"] = {
                "type": "slack",
                "bot_token": "env:SLACK_BOT_TOKEN",
                "channel": self._slack_channel,
            }
            console.print(
                f"[green]✓[/green] Default notifier: [bold]Slack[/bold] → "
                f"[dim]{self._slack_channel}[/dim] (from Step 2)"
            )
        else:
            console.print("[dim]No Slack configured — you can add a notifier manually here.[/dim]")

        if not _ask_yes_no("Add notifiers for this profile?", default=False):
            return notifiers

        NOTIFIER_TYPES = [
            ("Slack channel",        "slack"),
            ("Discord webhook",      "discord"),
            ("Generic HTTP webhook", "webhook"),
        ]

        while True:
            console.print()
            console.print("  Notifier types:")
            for i, (label, _) in enumerate(NOTIFIER_TYPES, 1):
                console.print(f"    [{i}] {label}")
            console.print()

            raw = input("  Select type (or Enter to finish): ").strip()
            if not raw:
                break
            try:
                ntype_label, ntype = NOTIFIER_TYPES[int(raw) - 1]
            except (ValueError, IndexError):
                console.print("  [red]Invalid choice.[/red]")
                continue

            notifier_name = input("  Notifier name (e.g. discord-ops, pagerduty): ").strip()
            if not notifier_name:
                console.print("  [yellow]! Name required — skipping.[/yellow]")
                continue
            if notifier_name in notifiers:
                console.print(f"  [yellow]! '{notifier_name}' already exists — skipping.[/yellow]")
                continue

            if ntype == "slack":
                token = input("  Slack bot token (xoxb-...) [reuse SLACK_BOT_TOKEN]: ").strip()
                channel = input("  Channel [#incidents]: ").strip() or "#incidents"
                entry: dict[str, str] = {"type": "slack", "channel": channel}
                if token:
                    env_var = f"SLACK_BOT_TOKEN_{notifier_name.upper().replace('-', '_')}"
                    self._env[env_var] = token
                    entry["bot_token"] = f"env:{env_var}"
                else:
                    entry["bot_token"] = "env:SLACK_BOT_TOKEN"
                notifiers[notifier_name] = entry

            elif ntype == "discord":
                webhook_url = input("  Discord webhook URL: ").strip()
                if not webhook_url:
                    console.print("  [yellow]! URL required — skipping.[/yellow]")
                    continue
                env_var = f"DISCORD_WEBHOOK_{notifier_name.upper().replace('-', '_')}"
                self._env[env_var] = webhook_url
                notifiers[notifier_name] = {"type": "discord", "webhook_url": f"env:{env_var}"}

            elif ntype == "webhook":
                url = input("  Webhook URL: ").strip()
                if not url:
                    console.print("  [yellow]! URL required — skipping.[/yellow]")
                    continue
                env_var = f"WEBHOOK_{notifier_name.upper().replace('-', '_')}_URL"
                self._env[env_var] = url
                entry = {"type": "webhook", "url": f"env:{env_var}"}
                auth_header = input("  Authorization header value (leave blank if none): ").strip()
                if auth_header:
                    auth_var = f"WEBHOOK_{notifier_name.upper().replace('-', '_')}_AUTH"
                    self._env[auth_var] = auth_header
                    entry["header_Authorization"] = f"env:{auth_var}"
                notifiers[notifier_name] = entry

            console.print(f"  [green]✓[/green] Added notifier: [bold]{notifier_name}[/bold] ({ntype_label})")

        if notifiers:
            console.print(f"[green]✓[/green] {len(notifiers)} notifier(s): {', '.join(notifiers)}")
        return notifiers

    def _collect_services_for_profile(self) -> dict:
        """Collect per-service config for one profile. Returns a services dict."""
        console.print()
        FORMAT_CHOICES = ["label", "json", "logfmt", "pattern"]
        services: dict[str, dict] = {}

        if not _ask_yes_no("Add per-service config for this profile?", default=False):
            return services

        console.print(
            "[dim]Configure per-service log format and repo mappings.\n"
            "Example: payments-api → repo=acme/payments, format=json[/dim]"
        )
        while True:
            svc = input("  Service name (or Enter to finish): ").strip()
            if not svc:
                break

            repo = input(f"  GitHub repo for {svc} (owner/repo, optional): ").strip()
            resource_type = input(f"  Resource type (ecs/lambda/eks/cloudrun, optional): ").strip()

            console.print("  Log formats:")
            hints = {
                "label": "level is a stream label (fastest)",
                "json":  '{"level":"error","msg":"..."}',
                "logfmt": "level=error msg=...",
                "pattern": "2026-01-01 ERROR SomeClass: ...",
            }
            for i, fmt in enumerate(FORMAT_CHOICES, 1):
                console.print(f"    [{i}] {fmt:<8}  [dim]{hints[fmt]}[/dim]")
            while True:
                raw = input("  Select format [1]: ").strip() or "1"
                try:
                    fmt = FORMAT_CHOICES[int(raw) - 1]
                    break
                except (ValueError, IndexError):
                    console.print("  [red]Invalid choice.[/red]")

            level_field = input("  Level field name [level]: ").strip() or "level"

            entry: dict[str, str] = {"log_format": fmt, "log_level_field": level_field}
            if repo:
                entry["repo"] = repo
            if resource_type:
                entry["resource_type"] = resource_type
            services[svc] = entry
            console.print(f"  [green]✓[/green] {svc}: format={fmt}" + (f", repo={repo}" if repo else ""))

        return services

    def _step_github(self) -> None:
        console.print()
        console.print(Rule("[bold]Step 3 — GitHub (for code investigation and auto-PRs)[/bold]"))
        console.print()
        console.print(
            "[dim]Tinker uses GitHub to read code (stack trace files, search, commits)\n"
            "and to open fix PRs. Without this, [bold]fix[/bold] and [bold]approve[/bold] won't work.[/dim]"
        )
        console.print()

        if not _ask_yes_no("Set up GitHub integration?", default=True):
            console.print("[yellow]! Skipped — fix and approve commands will be unavailable.[/yellow]")
            return

        token = input("GitHub token (ghp_... or classic PAT with repo scope): ").strip()
        if not token:
            console.print("[yellow]! No token entered — skipping GitHub setup.[/yellow]")
            return

        # Validate token
        console.print("[dim]Validating token...[/dim]")
        try:
            from github import Github
            gh = Github(token)
            user = gh.get_user().login
            console.print(f"[green]✓ Authenticated as:[/green] {user}")
        except Exception as exc:
            console.print(f"[yellow]! Token validation failed: {str(exc)[:60]}[/yellow]")
            console.print("[dim]Continuing anyway — check the token if fix/approve fail.[/dim]")

        self._env["GITHUB_TOKEN"] = token

        # Collect default repo — structure → TOML
        console.print()
        console.print(
            "[dim]Enter the default repository (used when no service-specific mapping exists).[/dim]"
        )
        default_repo = input("Default repository (owner/repo): ").strip()
        github_section: dict[str, str] = {"token": "env:GITHUB_TOKEN"}
        if default_repo:
            github_section["default_repo"] = default_repo
        self._toml["github"] = github_section

        console.print(
            "[dim]Per-service repo mappings can be added per profile in Step 5.[/dim]"
        )

    def _step_api_key(self) -> None:
        console.print()
        console.print(Rule("[bold]Step 4 — Server API Key[/bold]"))
        console.print()
        console.print(
            "The CLI authenticates to this server with an API key.\n"
            "Generate one now and share the raw key with CLI users.\n"
            "The server stores only the SHA-256 hash.\n"
        )

        raw_key = secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        # Hashed key entry goes into TOML [auth] — the hash is not secret
        self._toml["auth"] = {
            "api_keys": [
                {"hash": key_hash, "subject": "admin", "roles": ["oncall"]},
            ],
        }

        console.print(Panel(
            f"[bold]Raw API key[/bold] (give this to CLI users):\n\n"
            f"[bold cyan]{raw_key}[/bold cyan]\n\n"
            "[dim]Store it somewhere safe — it won't be shown again.[/dim]",
            border_style="yellow",
        ))
        input("Press Enter once you have copied the key...")
        console.print()

    def _write_env(self) -> None:
        """Write secrets-only .env file."""
        if not self._env:
            return
        console.print()
        console.print(Rule("[bold]Writing secrets (.env)[/bold]"))
        lines = [
            "# Tinker secrets — DO NOT COMMIT",
            "# Generated by `tinker init server`",
            "# Location: ~/.tinker/.env",
            "",
        ]
        for k, v in self._env.items():
            if '"' in v:
                lines.append(f"{k}='{v}'")
            elif " " in v:
                lines.append(f'{k}="{v}"')
            else:
                lines.append(f"{k}={v}")

        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        self.env_file.write_text("\n".join(lines) + "\n")
        console.print(f"[green]✓ Written:[/green] {self.env_file}")

    def _write_toml(self) -> None:
        """Write structural config to config.toml using manual TOML serialisation."""
        console.print()
        console.print(Rule("[bold]Writing config.toml[/bold]"))

        lines: list[str] = [
            "# Tinker server configuration",
            "# Generated by `tinker init server` / `tinker profile add`",
            "# Secrets are in ~/.tinker/.env — reference them here as env:VAR_NAME",
            "",
        ]

        def _val(v: object) -> str:
            if isinstance(v, bool):
                return "true" if v else "false"
            if isinstance(v, int):
                return str(v)
            return f'"{v}"'

        def _write_section(key: str, data: dict) -> None:
            lines.append(f"[{key}]")
            for k, v in data.items():
                if not isinstance(v, (dict, list)):
                    lines.append(f"{k} = {_val(v)}")
            lines.append("")

        # active_profile
        if active := self._toml.get("active_profile"):
            lines.append(f'active_profile = "{active}"')
            lines.append("")

        # [server]
        lines += ["[server]", 'host = "0.0.0.0"', "port = 8000", ""]

        # [llm]
        if llm := self._toml.get("llm"):
            _write_section("llm", llm)  # type: ignore[arg-type]

        # [auth] — api_keys as array of inline tables
        if auth := self._toml.get("auth"):
            lines.append("[auth]")
            key_entries = auth.get("api_keys", [])  # type: ignore[union-attr]
            for entry in key_entries:
                roles_str = ", ".join(f'"{r}"' for r in entry.get("roles", []))
                lines.append(
                    f'api_keys = [{{hash = "{entry["hash"]}", '
                    f'subject = "{entry["subject"]}", '
                    f"roles = [{roles_str}]}}]"
                )
            lines.append("")

        # [slack]
        if slack := self._toml.get("slack"):
            _write_section("slack", slack)  # type: ignore[arg-type]

        # [github]
        if github := self._toml.get("github"):
            _write_section("github", github)  # type: ignore[arg-type]

        # [profiles.*]  — each profile's scalar keys, then nested notifiers + services
        for pname, profile in (self._toml.get("profiles") or {}).items():  # type: ignore[union-attr]
            lines.append(f"[profiles.{pname}]")
            for k, v in profile.items():  # type: ignore[union-attr]
                if not isinstance(v, dict):
                    lines.append(f"{k} = {_val(v)}")
            lines.append("")

            for nname, notifier in (profile.get("notifiers") or {}).items():  # type: ignore[union-attr]
                lines.append(f"[profiles.{pname}.notifiers.{nname}]")
                for k, v in notifier.items():
                    lines.append(f"{k} = {_val(v)}")
                lines.append("")

            for sname, svc in (profile.get("services") or {}).items():  # type: ignore[union-attr]
                lines.append(f"[profiles.{pname}.services.{sname}]")
                for k, v in svc.items():
                    lines.append(f"{k} = {_val(v)}")
                lines.append("")

        self.toml_file.parent.mkdir(parents=True, exist_ok=True)
        self.toml_file.write_text("\n".join(lines) + "\n")
        console.print(f"[green]✓ Written:[/green] {self.toml_file}")

    def _show_next_steps(self) -> None:
        console.print()
        console.print(Panel(
            "[bold]Setup complete![/bold]\n\n"
            f"Config:  [dim]{self.toml_file}[/dim]\n"
            f"Secrets: [dim]{self.env_file}[/dim]\n\n"
            "Profile commands:\n\n"
            "  [bold cyan]tinker profile list[/bold cyan]          — show all profiles\n"
            "  [bold cyan]tinker profile use <name>[/bold cyan]    — switch active profile\n"
            "  [bold cyan]tinker profile add[/bold cyan]           — add a new cloud profile\n\n"
            "Start the server:\n\n"
            "  [bold cyan]tinker server[/bold cyan]\n\n"
            "Then on each developer laptop:\n\n"
            "  [bold cyan]tinker init cli[/bold cyan]",
            border_style="green",
        ))


# ══════════════════════════════════════════════════════════════════════════════
# CLIWizard
# ══════════════════════════════════════════════════════════════════════════════

class CLIWizard:
    """Wizard for pointing a developer's CLI at an existing Tinker server."""

    def run(self) -> None:
        console.print(Panel.fit(
            "[bold cyan]Tinker CLI Setup[/bold cyan]\n"
            "Connect this machine's CLI to a running Tinker server.\n\n"
            "[dim]Press Ctrl+C at any time to exit.[/dim]",
            border_style="cyan",
        ))
        console.print()

        try:
            self._run()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Setup cancelled.[/yellow]")
            sys.exit(0)

    def _run(self) -> None:
        import asyncio

        # ── URL ───────────────────────────────────────────────────────────────
        console.print("[dim]Where is your Tinker server running?[/dim]")
        console.print("[dim](Run [bold]tinker server[/bold] on any machine with cloud access.)[/dim]\n")

        current_url = _read_current_url()
        prompt = f"Tinker server URL [{current_url}]: " if current_url else "Tinker server URL [http://localhost:8000]: "
        raw_url = input(prompt).strip()
        url = raw_url or current_url or "http://localhost:8000"
        url = url.rstrip("/")

        # ── Token ─────────────────────────────────────────────────────────────
        console.print()
        current_token = os.environ.get("TINKER_API_TOKEN", "")
        token_hint = " (press Enter to keep existing)" if current_token else ""
        token = input(f"API token{token_hint}: ").strip() or current_token

        if not token:
            console.print(
                "[yellow]No token provided.[/yellow]\n"
                "[dim]The server admin can generate one with [bold]tinker init server[/bold].[/dim]"
            )

        # ── Test connection ───────────────────────────────────────────────────
        console.print()
        console.print("[dim]Testing connection...[/dim]")

        os.environ["TINKER_SERVER_URL"] = url
        if token:
            os.environ["TINKER_API_TOKEN"] = token

        try:
            from tinker.client import get_client
            client = get_client(url_override=url)
            data = asyncio.run(client.health())
            console.print(
                f"[green]✓ Connected:[/green] "
                f"Tinker v{data.get('version', '?')}  "
                f"backend={data.get('backend', '?')}"
            )
        except Exception as exc:
            console.print(f"[yellow]! Connection failed: {str(exc)[:80]}[/yellow]")
            console.print(
                "[dim]Saving config anyway. Check that the server is running "
                "and the token is correct.[/dim]"
            )

        # ── Write ~/.tinker/config ────────────────────────────────────────────
        from tinker.client.config import write_config
        config_path = write_config(url, token=token or None)
        console.print(f"[green]✓ Saved:[/green] {config_path}")

        console.print()
        console.print(Panel(
            "[bold]All set![/bold]\n\n"
            f"Config saved to [dim]{config_path}[/dim]\n\n"
            "Try it:\n\n"
            "  [bold cyan]tinker doctor[/bold cyan]\n"
            "  [bold cyan]tinker anomaly <your-service>[/bold cyan]",
            border_style="green",
        ))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_cloud() -> dict | None:
    """Try to detect the cloud environment from instance metadata."""
    import urllib.request

    # AWS — IMDS v2
    try:
        req = urllib.request.Request(
            "http://169.254.169.254/latest/meta-data/placement/region",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "10"},
            method="PUT",
        )
        token = urllib.request.urlopen(req, timeout=1).read().decode()
        req2 = urllib.request.Request(
            "http://169.254.169.254/latest/meta-data/placement/region",
            headers={"X-aws-ec2-metadata-token": token},
        )
        region = urllib.request.urlopen(req2, timeout=1).read().decode()
        if region:
            return {"label": f"AWS ({region})", "backend": "cloudwatch", "cloud": "aws"}
    except Exception:
        pass

    # GCP — metadata server
    try:
        req = urllib.request.Request(
            "http://metadata.google.internal/computeMetadata/v1/project/project-id",
            headers={"Metadata-Flavor": "Google"},
        )
        project = urllib.request.urlopen(req, timeout=1).read().decode()
        if project:
            return {"label": f"GCP ({project})", "backend": "gcp", "cloud": "gcp"}
    except Exception:
        pass

    # Azure — IMDS
    try:
        req = urllib.request.Request(
            "http://169.254.169.254/metadata/instance?api-version=2021-02-01",
            headers={"Metadata": "true"},
        )
        import json
        data = json.loads(urllib.request.urlopen(req, timeout=1).read())
        sub = data.get("compute", {}).get("subscriptionId", "")
        if sub:
            return {"label": "Azure", "backend": "azure", "cloud": "azure"}
    except Exception:
        pass

    return None


def _ask_yes_no(question: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    raw = input(f"{question} {hint} ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _read_current_url() -> str:
    from tinker.client.config import _read_config
    return os.environ.get("TINKER_SERVER_URL", "") or _read_config().get("url", "")

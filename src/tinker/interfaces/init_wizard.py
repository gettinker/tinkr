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
        self._env: dict[str, str] = {}

    def run(self) -> None:
        console.print(Panel.fit(
            "[bold cyan]Tinker Server Setup[/bold cyan]\n"
            "This wizard configures the server that runs in your cloud environment.\n\n"
            "[dim]Press Ctrl+C at any time to exit.[/dim]",
            border_style="cyan",
        ))
        console.print()

        try:
            self._step_cloud()
            self._step_llm()
            self._step_slack()
            self._step_github()
            self._step_api_key()
            self._write_env()
            self._show_next_steps()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Setup cancelled.[/yellow]")
            sys.exit(0)

    # ── Steps ─────────────────────────────────────────────────────────────────

    def _step_cloud(self) -> None:
        console.print(Rule("[bold]Step 1 — Cloud / Observability Backend[/bold]"))

        # Try auto-detection first
        detected = _detect_cloud()
        if detected:
            console.print(f"[green]Auto-detected:[/green] [bold]{detected['label']}[/bold]")
            use_detected = _ask_yes_no(f"Use {detected['label']}?", default=True)
            if use_detected:
                backend = detected["backend"]
                cloud = detected["cloud"]
                self._env["TINKER_BACKEND"] = backend
                self._configure_cloud(cloud)
                return

        # Manual selection
        console.print()
        for i, (label, backend, cloud) in enumerate(CLOUD_CHOICES, 1):
            console.print(f"  [{i}] {label}")
        console.print()

        while True:
            raw = input("Select cloud [1]: ").strip() or "1"
            try:
                idx = int(raw) - 1
                label, backend, cloud = CLOUD_CHOICES[idx]
                break
            except (ValueError, IndexError):
                console.print("[red]Invalid choice.[/red]")

        self._env["TINKER_BACKEND"] = backend
        self._configure_cloud(cloud)

    def _configure_cloud(self, cloud: str) -> None:
        console.print()
        if cloud == "aws":
            self._check_aws_permissions()
        elif cloud == "gcp":
            self._check_gcp_permissions()
        elif cloud == "azure":
            self._check_azure_permissions()
        elif cloud == "grafana":
            self._configure_grafana()
        elif cloud == "datadog":
            self._configure_datadog()
        elif cloud == "elastic":
            self._configure_elastic()

    def _check_aws_permissions(self) -> None:
        console.print("[dim]Checking AWS CloudWatch permissions...[/dim]")
        try:
            import boto3
            client = boto3.client("logs")
            client.describe_log_groups(limit=1)
            console.print("[green]✓ CloudWatch Logs read access confirmed.[/green]")
        except Exception as exc:
            msg = str(exc)
            if "credential" in msg.lower() or "NoCredentials" in str(type(exc)):
                console.print("[yellow]✗ No AWS credentials found.[/yellow]")
                console.print(
                    "[dim]Attach an IAM role to this instance with the following policy:[/dim]"
                )
                console.print(Syntax(AWS_POLICY, "json", theme="monokai"))
            else:
                console.print(f"[yellow]! CloudWatch check: {msg[:80]}[/yellow]")
                console.print(
                    "[dim]If this is a permissions error, attach the policy above "
                    "to your IAM role.[/dim]"
                )

        region = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or ""
        if not region:
            region = input("AWS region [us-east-1]: ").strip() or "us-east-1"
        self._env["AWS_REGION"] = region

    def _check_gcp_permissions(self) -> None:
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
        if project:
            self._env["GCP_PROJECT_ID"] = project

    def _check_azure_permissions(self) -> None:
        console.print("[dim]Checking Azure Monitor permissions...[/dim]")
        workspace_id = os.environ.get("AZURE_WORKSPACE_ID") or ""
        if not workspace_id:
            workspace_id = input("Log Analytics workspace ID: ").strip()
        if workspace_id:
            self._env["AZURE_WORKSPACE_ID"] = workspace_id

        try:
            from azure.identity import DefaultAzureCredential
            from azure.monitor.query import LogsQueryClient
            from datetime import datetime, timedelta, timezone
            cred = DefaultAzureCredential()
            qc = LogsQueryClient(cred)
            qc.query_workspace(
                workspace_id,
                "AzureDiagnostics | take 1",
                timespan=timedelta(minutes=5),
            )
            console.print("[green]✓ Log Analytics read access confirmed.[/green]")
        except Exception as exc:
            console.print(f"[yellow]! Azure check: {str(exc)[:80]}[/yellow]")
            console.print("[dim]Required role assignments:[/dim]")
            console.print(Syntax(AZURE_IAM_COMMANDS, "bash", theme="monokai"))

    def _configure_grafana(self) -> None:
        loki = input("Loki URL [http://localhost:3100]: ").strip() or "http://localhost:3100"
        prom = input("Prometheus URL [http://localhost:9090]: ").strip() or "http://localhost:9090"
        api_key = input("Grafana API key (leave blank for no auth): ").strip()
        self._env["GRAFANA_LOKI_URL"] = loki
        self._env["GRAFANA_PROMETHEUS_URL"] = prom
        if api_key:
            self._env["GRAFANA_API_KEY"] = api_key

        console.print()
        console.print("[dim]Loki service label — the stream selector label your log shipper sets for the service name.[/dim]")
        console.print("[dim]Common values: service (Promtail default), app (Helm charts), job, service_name, container[/dim]")
        svc_label = input("Service label [service]: ").strip() or "service"
        if svc_label != "service":
            self._env["GRAFANA_SERVICE_LABEL"] = svc_label

    def _configure_datadog(self) -> None:
        api_key = input("Datadog API key: ").strip()
        app_key = input("Datadog App key: ").strip()
        site = input("Datadog site [datadoghq.com]: ").strip() or "datadoghq.com"
        if api_key:
            self._env["DATADOG_API_KEY"] = api_key
        if app_key:
            self._env["DATADOG_APP_KEY"] = app_key
        self._env["DATADOG_SITE"] = site

    def _configure_elastic(self) -> None:
        url = input("Elasticsearch URL [http://localhost:9200]: ").strip() or "http://localhost:9200"
        api_key = input("Elasticsearch API key (leave blank for no auth): ").strip()
        self._env["ELASTICSEARCH_URL"] = url
        if api_key:
            self._env["ELASTICSEARCH_API_KEY"] = api_key

    def _step_llm(self) -> None:
        console.print()
        console.print(Rule("[bold]Step 2 — LLM Provider & Model[/bold]"))

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

        self._env["TINKER_DEFAULT_MODEL"] = default_model
        self._env["TINKER_DEEP_RCA_MODEL"] = deep_model
        console.print(f"[green]✓[/green] Default model:  [dim]{default_model}[/dim]")
        console.print(f"[green]✓[/green] Deep RCA model: [dim]{deep_model}[/dim]")

    def _step_slack(self) -> None:
        console.print()
        console.print(Rule("[bold]Step 3 — Slack (optional)[/bold]"))
        console.print()

        if not _ask_yes_no("Set up Slack alerts?", default=False):
            return

        bot_token = input("Slack bot token (xoxb-...): ").strip()
        if not bot_token:
            return
        self._env["SLACK_BOT_TOKEN"] = bot_token

        channel = input("Default alert channel [#incidents]: ").strip() or "#incidents"
        self._env["SLACK_ALERTS_CHANNEL"] = channel

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

    def _step_github(self) -> None:
        console.print()
        console.print(Rule("[bold]Step 4 — GitHub (for code investigation and auto-PRs)[/bold]"))
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

        # Collect repos — one default + optional service-specific overrides
        console.print()
        console.print(
            "[dim]Enter the default repository (used when no service-specific mapping exists).[/dim]"
        )
        default_repo = input("Default repository (owner/repo): ").strip()
        if default_repo:
            self._env["GITHUB_REPO"] = default_repo

        # Service-specific repos
        console.print()
        console.print(
            "[dim]If different services live in different repos, add per-service mappings.\n"
            "Example: payments-api → acme/payments, auth-service → acme/auth[/dim]"
        )
        if _ask_yes_no("Add service-specific repo mappings?", default=False):
            repos_map: dict[str, str] = {}
            while True:
                svc = input("  Service name (or Enter to finish): ").strip()
                if not svc:
                    break
                repo = input(f"  Repo for {svc} (owner/repo): ").strip()
                if repo:
                    repos_map[svc] = repo
                    console.print(f"  [green]✓[/green] {svc} → {repo}")
            if repos_map:
                import json
                self._env["GITHUB_REPOS"] = json.dumps(repos_map)

    def _step_api_key(self) -> None:
        console.print()
        console.print(Rule("[bold]Step 5 — Server API Key[/bold]"))
        console.print()
        console.print(
            "The CLI authenticates to this server with an API key.\n"
            "Generate one now and share the raw key with CLI users.\n"
            "The server stores only the SHA-256 hash.\n"
        )

        raw_key = secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        hashed_entry = f'{{"hash": "{key_hash}", "subject": "admin", "roles": ["oncall"]}}'
        self._env["TINKER_API_KEYS"] = f"[{hashed_entry}]"

        console.print(Panel(
            f"[bold]Raw API key[/bold] (give this to CLI users):\n\n"
            f"[bold cyan]{raw_key}[/bold cyan]\n\n"
            "[dim]Store it somewhere safe — it won't be shown again.[/dim]",
            border_style="yellow",
        ))

    def _write_env(self) -> None:
        console.print()
        console.print(Rule("[bold]Writing configuration[/bold]"))
        lines = [
            "# Tinker Server configuration",
            "# Generated by `tinker init server`",
            "# Location: ~/.tinker/.env — loaded automatically by `tinker server`",
            "",
        ]
        for k, v in self._env.items():
            if '"' in v:
                # JSON values contain double quotes — wrap in single quotes
                lines.append(f"{k}='{v}'")
            elif " " in v or "[" in v:
                lines.append(f'{k}="{v}"')
            else:
                lines.append(f"{k}={v}")

        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        self.env_file.write_text("\n".join(lines) + "\n")
        console.print(f"[green]✓ Written:[/green] {self.env_file}")

    def _show_next_steps(self) -> None:
        console.print()
        console.print(Panel(
            "[bold]Setup complete![/bold]\n\n"
            "Start the server:\n\n"
            f"  [bold cyan]tinker server[/bold cyan]\n\n"
            "Or with a custom port:\n\n"
            f"  [bold cyan]tinker server --port 9000[/bold cyan]\n\n"
            "Then on each developer laptop:\n\n"
            f"  [bold cyan]tinker init cli[/bold cyan]",
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

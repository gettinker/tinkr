"""tinker init — interactive setup wizard.

Guides the user through:
  1. Cloud provider + backend selection
  2. IAM / permission setup (show commands or create automatically)
  3. LLM provider + model selection
  4. Optional integrations (Slack, GitHub)
  5. Writing .env and tinker.toml
  6. Optionally deploying the server
"""

from __future__ import annotations

import hashlib
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any

import questionary
import structlog
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax

log = structlog.get_logger(__name__)
console = Console()

# ── Cloud / backend catalogues ────────────────────────────────────────────────

CLOUD_CHOICES = [
    questionary.Choice("AWS",                  value="aws"),
    questionary.Choice("GCP (Google Cloud)",   value="gcp"),
    questionary.Choice("Azure",                value="azure"),
    questionary.Choice("Self-hosted (Grafana + Prometheus)", value="grafana"),
    questionary.Choice("Datadog",              value="datadog"),
    questionary.Choice("Elastic / OpenSearch", value="elastic"),
]

DEPLOY_TARGET = {
    "aws":     ["AWS ECS Fargate (recommended)", "Docker Compose (local/VM)"],
    "gcp":     ["GCP Cloud Run (recommended)",   "Docker Compose (local/VM)"],
    "azure":   ["Azure Container Apps (recommended)", "Docker Compose (local/VM)"],
    "grafana": ["Docker Compose"],
    "datadog": ["Docker Compose"],
    "elastic": ["Docker Compose"],
}

LLM_CHOICES = [
    questionary.Choice("Anthropic (Claude) — direct",       value="anthropic"),
    questionary.Choice("OpenRouter — access 100+ models",   value="openrouter"),
    questionary.Choice("OpenAI (GPT-4o etc.)",              value="openai"),
    questionary.Choice("Groq — fast open-source models",    value="groq"),
    questionary.Choice("Ollama — local models",             value="ollama"),
]

OPENROUTER_MODELS = [
    questionary.Choice("anthropic/claude-sonnet-4-6  (recommended)", value="openrouter/anthropic/claude-sonnet-4-6"),
    questionary.Choice("anthropic/claude-opus-4-6",                  value="openrouter/anthropic/claude-opus-4-6"),
    questionary.Choice("openai/gpt-4o",                              value="openrouter/openai/gpt-4o"),
    questionary.Choice("openai/gpt-4o-mini (cheaper)",               value="openrouter/openai/gpt-4o-mini"),
    questionary.Choice("meta-llama/llama-3.1-70b-instruct (free)",   value="openrouter/meta-llama/llama-3.1-70b-instruct"),
    questionary.Choice("google/gemini-pro-1.5",                      value="openrouter/google/gemini-pro-1.5"),
]


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

AWS_SETUP_COMMANDS = """\
# 1. Create the role
aws iam create-role \\
  --role-name tinker-readonly \\
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

# 2. Attach the policy (saved to /tmp/tinker-policy.json)
aws iam put-role-policy \\
  --role-name tinker-readonly \\
  --policy-name TinkerReadOnly \\
  --policy-document file:///tmp/tinker-policy.json
"""

GCP_SETUP_COMMANDS = """\
# 1. Create the service account
gcloud iam service-accounts create tinker-readonly \\
  --display-name "Tinker read-only"

# 2. Grant read-only roles
gcloud projects add-iam-policy-binding PROJECT_ID \\
  --member="serviceAccount:tinker-readonly@PROJECT_ID.iam.gserviceaccount.com" \\
  --role="roles/logging.viewer"

gcloud projects add-iam-policy-binding PROJECT_ID \\
  --member="serviceAccount:tinker-readonly@PROJECT_ID.iam.gserviceaccount.com" \\
  --role="roles/monitoring.viewer"
"""

AZURE_SETUP_COMMANDS = """\
# Enable system-assigned managed identity on the Container App (done at deploy time).
# Assign read roles after the app is created:

az role assignment create \\
  --assignee <MANAGED_IDENTITY_PRINCIPAL_ID> \\
  --role "Monitoring Reader" \\
  --scope /subscriptions/SUBSCRIPTION_ID

az role assignment create \\
  --assignee <MANAGED_IDENTITY_PRINCIPAL_ID> \\
  --role "Log Analytics Reader" \\
  --scope /subscriptions/SUBSCRIPTION_ID
"""


# ── Wizard ────────────────────────────────────────────────────────────────────

class InitWizard:
    def __init__(self, env_file: Path = Path(".env"), config_file: Path = Path("tinker.toml")) -> None:
        self.env_file = env_file
        self.config_file = config_file
        self.config: dict[str, str] = {}

    def run(self) -> None:
        console.print(Panel.fit(
            "[bold cyan]Welcome to Tinker![/bold cyan]\n"
            "Let's get you set up in a few steps.\n\n"
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
            self._write_files()
            self._step_deploy()
        except (KeyboardInterrupt, questionary.Abort):
            console.print("\n[yellow]Setup cancelled.[/yellow]")
            sys.exit(0)

    # ── Step 1: Cloud + backend ───────────────────────────────────────────────

    def _step_cloud(self) -> None:
        console.print(Rule("[bold]Step 1 of 5 — Cloud & Observability[/bold]"))

        cloud = questionary.select(
            "Which cloud provider / observability stack are you using?",
            choices=CLOUD_CHOICES,
        ).ask()

        self.config["TINKER_BACKEND"] = cloud

        deploy_options = DEPLOY_TARGET[cloud]
        deploy = questionary.select(
            "Where will the Tinker server run?",
            choices=deploy_options,
        ).ask()
        self.config["_deploy_target"] = deploy

        # Cloud-specific vars
        if cloud == "aws":
            self.config["AWS_REGION"] = questionary.text(
                "AWS region?", default="us-east-1"
            ).ask()
            self._show_aws_permissions()

        elif cloud == "gcp":
            self.config["GCP_PROJECT_ID"] = questionary.text("GCP project ID?").ask()
            self._show_gcp_permissions()

        elif cloud == "azure":
            self.config["AZURE_LOG_ANALYTICS_WORKSPACE_ID"] = questionary.text(
                "Log Analytics workspace ID?"
            ).ask()
            self.config["AZURE_SUBSCRIPTION_ID"] = questionary.text(
                "Azure subscription ID?"
            ).ask()
            self.config["AZURE_RESOURCE_GROUP"] = questionary.text(
                "Resource group name?"
            ).ask()
            self._show_azure_permissions()

        elif cloud == "grafana":
            self.config["GRAFANA_LOKI_URL"] = questionary.text(
                "Loki URL?", default="http://localhost:3100"
            ).ask()
            self.config["GRAFANA_PROMETHEUS_URL"] = questionary.text(
                "Prometheus URL?", default="http://localhost:9090"
            ).ask()
            self.config["GRAFANA_TEMPO_URL"] = questionary.text(
                "Tempo URL?", default="http://tempo:3200"
            ).ask()
            use_key = questionary.confirm("Do you need an API key? (Grafana Cloud)").ask()
            if use_key:
                self.config["GRAFANA_API_KEY"] = questionary.password("Grafana API key?").ask()

        elif cloud == "datadog":
            self.config["DATADOG_API_KEY"] = questionary.password("Datadog API key?").ask()
            self.config["DATADOG_APP_KEY"] = questionary.password("Datadog application key?").ask()
            self.config["DATADOG_SITE"] = questionary.text(
                "Datadog site?", default="datadoghq.com"
            ).ask()

        elif cloud == "elastic":
            self.config["ELASTICSEARCH_URL"] = questionary.text(
                "Elasticsearch URL?", default="http://localhost:9200"
            ).ask()
            self.config["ELASTICSEARCH_API_KEY"] = questionary.password(
                "Elasticsearch API key? (leave blank for no auth)"
            ).ask()

    def _show_aws_permissions(self) -> None:
        console.print()
        console.print(Panel(
            "[bold]IAM Role Setup[/bold]\n\n"
            "Tinker needs a read-only IAM role attached to the ECS task.\n"
            "No long-lived credentials — the role is assumed automatically.",
            border_style="yellow",
        ))
        console.print(Syntax(AWS_POLICY, "json", theme="monokai", line_numbers=False))
        console.print()

        auto = questionary.confirm(
            "Create the tinker-readonly IAM role automatically? (requires AWS admin credentials)"
        ).ask()
        if auto:
            self._create_aws_role()
        else:
            console.print("\n[dim]Run these commands when ready:[/dim]")
            console.print(Syntax(AWS_SETUP_COMMANDS, "bash", theme="monokai"))
            questionary.text("Press Enter to continue...").ask()

    def _show_gcp_permissions(self) -> None:
        console.print()
        console.print(Panel(
            "[bold]GCP Service Account Setup[/bold]\n\n"
            "Tinker needs a service account with logging.viewer and monitoring.viewer roles.\n"
            "Credentials are picked up automatically via Workload Identity on Cloud Run.",
            border_style="yellow",
        ))
        commands = GCP_SETUP_COMMANDS.replace(
            "PROJECT_ID", self.config.get("GCP_PROJECT_ID", "PROJECT_ID")
        )
        console.print(Syntax(commands, "bash", theme="monokai"))
        questionary.text("Run those commands, then press Enter to continue...").ask()

    def _show_azure_permissions(self) -> None:
        console.print()
        console.print(Panel(
            "[bold]Azure Managed Identity Setup[/bold]\n\n"
            "The Container App uses a system-assigned managed identity.\n"
            "Run these role assignments after the app is deployed.",
            border_style="yellow",
        ))
        console.print(Syntax(AZURE_SETUP_COMMANDS, "bash", theme="monokai"))
        questionary.text("Press Enter to continue...").ask()

    def _create_aws_role(self) -> None:
        """Attempt to create the IAM role via boto3."""
        import json
        import tempfile

        try:
            import boto3
            iam = boto3.client("iam", region_name=self.config.get("AWS_REGION", "us-east-1"))
        except ImportError:
            console.print("[red]boto3 not available. Run the commands manually.[/red]")
            return

        with console.status("[cyan]Creating tinker-readonly IAM role...[/cyan]"):
            try:
                assume_policy = json.dumps({
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Effect": "Allow",
                        "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }],
                })
                iam.create_role(
                    RoleName="tinker-readonly",
                    AssumeRolePolicyDocument=assume_policy,
                    Description="Tinker read-only observability role",
                )
                iam.put_role_policy(
                    RoleName="tinker-readonly",
                    PolicyName="TinkerReadOnly",
                    PolicyDocument=AWS_POLICY,
                )
                console.print("[green]✓ tinker-readonly IAM role created.[/green]")
            except iam.exceptions.EntityAlreadyExistsException:
                console.print("[yellow]tinker-readonly role already exists — skipping.[/yellow]")
            except Exception as exc:
                console.print(f"[red]Failed: {exc}[/red]")
                console.print("[dim]Create the role manually using the commands above.[/dim]")

    # ── Step 2: LLM ──────────────────────────────────────────────────────────

    def _step_llm(self) -> None:
        console.print()
        console.print(Rule("[bold]Step 2 of 5 — LLM Provider[/bold]"))

        provider = questionary.select(
            "Which LLM provider do you want to use?",
            choices=LLM_CHOICES,
        ).ask()

        if provider == "anthropic":
            key = questionary.password("Anthropic API key (sk-ant-...)?").ask()
            self.config["ANTHROPIC_API_KEY"] = key
            self.config["TINKER_DEFAULT_MODEL"] = "anthropic/claude-sonnet-4-6"
            self.config["TINKER_DEEP_RCA_MODEL"] = "anthropic/claude-opus-4-6"

        elif provider == "openrouter":
            key = questionary.password("OpenRouter API key (sk-or-...)?").ask()
            self.config["OPENROUTER_API_KEY"] = key
            default_model = questionary.select(
                "Default model (used for most queries)?",
                choices=OPENROUTER_MODELS,
            ).ask()
            deep_model = questionary.select(
                "Deep RCA model (used for --deep analysis)?",
                choices=OPENROUTER_MODELS,
                default=OPENROUTER_MODELS[1].value,  # claude-opus
            ).ask()
            self.config["TINKER_DEFAULT_MODEL"] = default_model
            self.config["TINKER_DEEP_RCA_MODEL"] = deep_model

        elif provider == "openai":
            key = questionary.password("OpenAI API key (sk-...)?").ask()
            self.config["OPENAI_API_KEY"] = key
            self.config["TINKER_DEFAULT_MODEL"] = "openai/gpt-4o"
            self.config["TINKER_DEEP_RCA_MODEL"] = "openai/gpt-4o"

        elif provider == "groq":
            key = questionary.password("Groq API key (gsk_...)?").ask()
            self.config["GROQ_API_KEY"] = key
            self.config["TINKER_DEFAULT_MODEL"] = "groq/llama-3.1-70b-versatile"
            self.config["TINKER_DEEP_RCA_MODEL"] = "groq/llama-3.1-70b-versatile"
            console.print("[yellow]Note: Groq models don't support tool use in all configs.[/yellow]")

        elif provider == "ollama":
            model = questionary.text("Ollama model name?", default="llama3").ask()
            self.config["TINKER_DEFAULT_MODEL"] = f"ollama/{model}"
            self.config["TINKER_DEEP_RCA_MODEL"] = f"ollama/{model}"

    # ── Step 3: Slack ─────────────────────────────────────────────────────────

    def _step_slack(self) -> None:
        console.print()
        console.print(Rule("[bold]Step 3 of 5 — Slack (optional)[/bold]"))

        if not questionary.confirm("Enable Slack bot integration?", default=False).ask():
            return

        console.print(Panel(
            "Create a Slack app at [link=https://api.slack.com/apps]api.slack.com/apps[/link]\n"
            "Enable: Incoming Webhooks, Slash Commands, Socket Mode\n"
            "Add bot scopes: chat:write, commands, users.read, usergroups.read",
            title="Slack App Setup",
            border_style="blue",
        ))
        self.config["SLACK_BOT_TOKEN"] = questionary.password("Slack bot token (xoxb-...)?").ask()
        self.config["SLACK_SIGNING_SECRET"] = questionary.password("Slack signing secret?").ask()
        self.config["SLACK_ALERTS_CHANNEL"] = questionary.text(
            "Channel for proactive alerts?", default="#incidents"
        ).ask()

    # ── Step 4: GitHub ────────────────────────────────────────────────────────

    def _step_github(self) -> None:
        console.print()
        console.print(Rule("[bold]Step 4 of 5 — GitHub (optional)[/bold]"))

        if not questionary.confirm(
            "Enable GitHub integration? (opens PRs with suggested fixes)", default=False
        ).ask():
            return

        self.config["GITHUB_TOKEN"] = questionary.password("GitHub token (ghp_...)?").ask()
        self.config["GITHUB_REPO"] = questionary.text(
            "Repository? (org/repo)", default=""
        ).ask()
        self.config["TINKER_REPO_PATH"] = questionary.text(
            "Local path to the repository?", default=os.getcwd()
        ).ask()

    # ── Step 5: Server API key ────────────────────────────────────────────────

    def _step_api_key(self) -> None:
        console.print()
        console.print(Rule("[bold]Step 5 of 5 — Server Access[/bold]"))

        raw_key = secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        self.config["TINKER_API_KEYS"] = (
            f'[{{"hash":"{key_hash}","subject":"default","roles":["sre"]}}]'
        )
        self._generated_api_key = raw_key

        console.print(Panel(
            f"[bold]Your Tinker API key[/bold] (save this — it won't be shown again)\n\n"
            f"[green bold]{raw_key}[/green bold]\n\n"
            "[dim]Set this as TINKER_API_TOKEN in your shell or CI to use the CLI.[/dim]",
            border_style="green",
        ))

    # ── Write config files ────────────────────────────────────────────────────

    def _write_files(self) -> None:
        console.print()
        console.print(Rule("[bold]Writing configuration[/bold]"))

        # Write .env
        env_lines = [
            "# Generated by tinker init",
            f"# Generated at: {__import__('datetime').datetime.now().isoformat()}",
            "",
        ]
        skip_keys = {"_deploy_target"}
        for key, value in self.config.items():
            if key.startswith("_"):
                continue
            if any(secret in key.lower() for secret in ("key", "token", "secret", "password")):
                env_lines.append(f"{key}={value}")
            else:
                env_lines.append(f"{key}={value}")
        env_lines.append("")

        self.env_file.write_text("\n".join(env_lines))
        console.print(f"[green]✓[/green] Config written to [cyan]{self.env_file}[/cyan]")

        # Write tinker.toml
        deploy_target = self.config.get("_deploy_target", "Docker Compose")
        cloud = self.config.get("TINKER_BACKEND", "cloudwatch")
        toml = (
            f'# Tinker configuration — generated by tinker init\n'
            f'[deploy]\n'
            f'cloud = "{cloud}"\n'
            f'target = "{deploy_target}"\n'
        )
        if cloud == "aws":
            toml += f'region = "{self.config.get("AWS_REGION", "us-east-1")}"\n'
        elif cloud == "gcp":
            toml += f'project = "{self.config.get("GCP_PROJECT_ID", "")}"\n'
        self.config_file.write_text(toml)
        console.print(f"[green]✓[/green] Deploy config written to [cyan]{self.config_file}[/cyan]")

    # ── Deploy ────────────────────────────────────────────────────────────────

    def _step_deploy(self) -> None:
        console.print()
        if not questionary.confirm("Deploy the Tinker server now?", default=True).ask():
            console.print(
                "\n[dim]Run [bold]tinker deploy[/bold] when you're ready.[/dim]"
            )
            console.print(
                f"\n[dim]Set [bold]TINKER_API_TOKEN={self._generated_api_key}[/bold] in your shell.[/dim]"
            )
            return

        from tinker.interfaces.deploy import DeployEngine
        engine = DeployEngine.from_toml(self.config_file)
        engine.deploy()

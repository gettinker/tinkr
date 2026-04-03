"""tinker deploy — build the Docker image and deploy to the configured cloud.

Reads deploy config from tinker.toml (written by tinker init).
Supports: AWS ECS Fargate, GCP Cloud Run, Azure Container Apps, Docker Compose.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule

console = Console()


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command, streaming output to the terminal."""
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    result = subprocess.run(cmd, check=check)
    return result


class DeployEngine:
    def __init__(self, cloud: str, target: str, extra: dict[str, str]) -> None:
        self.cloud = cloud
        self.target = target
        self.extra = extra

    @classmethod
    def from_toml(cls, config_file: Path = Path("tinker.toml")) -> "DeployEngine":
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-reuse-def]

        data = tomllib.loads(config_file.read_text())
        deploy = data.get("deploy", {})
        return cls(
            cloud=deploy.get("cloud", "grafana"),
            target=deploy.get("target", "Docker Compose"),
            extra=deploy,
        )

    def deploy(self) -> None:
        console.print()
        console.print(Rule(f"[bold]Deploying to {self.cloud.upper()}[/bold]"))

        if "Docker Compose" in self.target:
            self._deploy_compose()
        elif self.cloud == "aws":
            self._deploy_aws()
        elif self.cloud == "gcp":
            self._deploy_gcp()
        elif self.cloud == "azure":
            self._deploy_azure()
        else:
            console.print(f"[yellow]No automated deploy for {self.cloud}. Use docker compose.[/yellow]")
            self._deploy_compose()

    # ── Docker Compose (local / self-hosted) ─────────────────────────────────

    def _deploy_compose(self) -> None:
        console.print(Panel(
            "Starting Tinker server with Docker Compose.\n"
            "This will also start Loki, Prometheus, and Grafana.",
            border_style="cyan",
        ))
        _run(["docker", "compose", "-f", "deploy/docker-compose.yml", "up", "--build", "-d"])
        console.print()
        console.print("[green]✓ Tinker server running at http://localhost:8000[/green]")
        console.print("[green]✓ Grafana UI at http://localhost:3000[/green]")
        console.print("[dim]Run [bold]tinker doctor[/bold] to verify connectivity.[/dim]")

    # ── AWS ECS Fargate ───────────────────────────────────────────────────────

    def _deploy_aws(self) -> None:
        import questionary
        region = self.extra.get("region", "us-east-1")

        # 1. Get AWS account ID
        with console.status("Getting AWS account ID..."):
            result = subprocess.run(
                ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                console.print("[red]AWS credentials not configured. Run `aws configure` first.[/red]")
                sys.exit(1)
            account_id = result.stdout.strip()

        ecr_url = f"{account_id}.dkr.ecr.{region}.amazonaws.com/tinker"

        # 2. Create ECR repo
        with console.status("Creating ECR repository..."):
            subprocess.run(
                ["aws", "ecr", "create-repository", "--repository-name", "tinker",
                 "--region", region],
                capture_output=True,
            )

        # 3. Docker login to ECR
        with console.status("Authenticating with ECR..."):
            token = subprocess.run(
                ["aws", "ecr", "get-login-password", "--region", region],
                capture_output=True, text=True,
            ).stdout.strip()
            subprocess.run(
                ["docker", "login", "--username", "AWS", "--password-stdin",
                 f"{account_id}.dkr.ecr.{region}.amazonaws.com"],
                input=token, text=True, check=True, capture_output=True,
            )

        # 4. Build + push
        console.print("[cyan]Building Docker image...[/cyan]")
        _run(["docker", "build", "-f", "deploy/Dockerfile", "-t", f"{ecr_url}:latest", "."])
        console.print("[cyan]Pushing to ECR...[/cyan]")
        _run(["docker", "push", f"{ecr_url}:latest"])

        # 5. Update task definition with real account/region
        import json
        td_path = Path("deploy/aws/task-definition.json")
        td = json.loads(td_path.read_text())
        td_str = json.dumps(td).replace("ACCOUNT_ID", account_id).replace("REGION", region)
        td = json.loads(td_str)
        td["containerDefinitions"][0]["image"] = f"{ecr_url}:latest"

        # 6. Register task definition
        with console.status("Registering ECS task definition..."):
            reg = subprocess.run(
                ["aws", "ecs", "register-task-definition",
                 "--cli-input-json", json.dumps(td),
                 "--region", region],
                capture_output=True, text=True,
            )
            if reg.returncode != 0:
                console.print(f"[red]Task definition registration failed:\n{reg.stderr}[/red]")
                self._show_manual_ecs_steps(account_id, region, ecr_url)
                return
            td_arn = json.loads(reg.stdout)["taskDefinition"]["taskDefinitionArn"]

        # 7. Create/update ECS service
        cluster = questionary.text("ECS cluster name?", default="tinker").ask()
        svc_name = "tinker"
        with console.status("Creating ECS service..."):
            # Try update first, create if not exists
            update = subprocess.run(
                ["aws", "ecs", "update-service",
                 "--cluster", cluster, "--service", svc_name,
                 "--task-definition", td_arn,
                 "--region", region],
                capture_output=True,
            )
            if update.returncode != 0:
                _run([
                    "aws", "ecs", "create-service",
                    "--cluster", cluster, "--service-name", svc_name,
                    "--task-definition", td_arn,
                    "--desired-count", "1",
                    "--launch-type", "FARGATE",
                    "--network-configuration",
                    "awsvpcConfiguration={subnets=[SUBNET_ID],securityGroups=[SG_ID],assignPublicIp=ENABLED}",
                    "--region", region,
                ])

        console.print(f"\n[green]✓ Tinker deployed to ECS cluster '{cluster}'[/green]")
        console.print("[dim]Attach a load balancer or use the task's public IP to access the server.[/dim]")

    def _show_manual_ecs_steps(self, account_id: str, region: str, ecr_url: str) -> None:
        console.print(Panel(
            "[bold]Manual ECS deployment steps:[/bold]\n\n"
            f"1. Update [cyan]deploy/aws/task-definition.json[/cyan]\n"
            f"   Replace ACCOUNT_ID → {account_id}\n"
            f"   Replace REGION → {region}\n"
            f"   Replace image URL → {ecr_url}:latest\n\n"
            "2. Register the task definition:\n"
            "   [cyan]aws ecs register-task-definition --cli-input-json file://deploy/aws/task-definition.json[/cyan]\n\n"
            "3. Create or update your ECS service to use the new task definition.",
            border_style="yellow",
        ))

    # ── GCP Cloud Run ─────────────────────────────────────────────────────────

    def _deploy_gcp(self) -> None:
        import questionary
        project = self.extra.get("project", "")
        if not project:
            project = questionary.text("GCP project ID?").ask()

        region = questionary.text("Cloud Run region?", default="us-central1").ask()
        image = f"gcr.io/{project}/tinker:latest"

        # Build + push via Cloud Build (no local Docker needed)
        use_cloud_build = questionary.confirm(
            "Use Cloud Build to build the image? (no local Docker needed)", default=True
        ).ask()

        if use_cloud_build:
            console.print("[cyan]Submitting build to Cloud Build...[/cyan]")
            _run(["gcloud", "builds", "submit",
                  "--tag", image,
                  "--dockerfile", "deploy/Dockerfile",
                  f"--project={project}",
                  "."])
        else:
            console.print("[cyan]Building and pushing Docker image...[/cyan]")
            _run(["docker", "build", "-f", "deploy/Dockerfile", "-t", image, "."])
            _run(["gcloud", "auth", "configure-docker", "--quiet"])
            _run(["docker", "push", image])

        # Update cloudrun.yaml with real project/image
        import re
        yaml_path = Path("deploy/gcp/cloudrun.yaml")
        yaml_text = yaml_path.read_text()
        yaml_text = yaml_text.replace("PROJECT_ID", project).replace(
            "REGION-docker.pkg.dev/PROJECT_ID/tinker/tinker:latest", image
        )

        # Deploy
        console.print("[cyan]Deploying to Cloud Run...[/cyan]")
        _run([
            "gcloud", "run", "services", "replace", "/dev/stdin",
            f"--project={project}", f"--region={region}",
        ])

        console.print(f"\n[green]✓ Tinker deployed to Cloud Run in {region}[/green]")
        console.print("[dim]Get the URL: gcloud run services describe tinker --region=" + region + "[/dim]")

    # ── Azure Container Apps ──────────────────────────────────────────────────

    def _deploy_azure(self) -> None:
        import questionary
        rg = self.extra.get("resource_group", "")
        if not rg:
            rg = questionary.text("Azure resource group?").ask()

        acr_name = questionary.text("Azure Container Registry name?").ask()
        image = f"{acr_name}.azurecr.io/tinker:latest"

        console.print("[cyan]Building and pushing to ACR...[/cyan]")
        _run(["az", "acr", "build",
              "--registry", acr_name,
              "--image", "tinker:latest",
              "--file", "deploy/Dockerfile",
              "."])

        console.print("[cyan]Deploying Container App...[/cyan]")
        _run([
            "az", "containerapp", "create",
            "--yaml", "deploy/azure/container-app.yaml",
            "--resource-group", rg,
        ])

        console.print(f"\n[green]✓ Tinker deployed to Azure Container Apps[/green]")
        console.print(f"[dim]Run: az containerapp show -n tinker -g {rg} --query properties.configuration.ingress.fqdn[/dim]")

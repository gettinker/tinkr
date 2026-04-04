"""Central configuration loaded from environment variables.

Resolution order (highest priority first):
  1. Environment variables (always win)
  2. ~/.tinker/.env  (user-level config, written by `tinker init server`)
  3. .env in current working directory (project-level override, optional)
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Ensure ~/.tinker/ exists so the DB and config can be written there
_tinker_dir = Path.home() / ".tinker"
_tinker_dir.mkdir(parents=True, exist_ok=True)

_USER_ENV = str(_tinker_dir / ".env")
_LOCAL_ENV = ".env"


class TinkerConfig(BaseSettings):
    model_config = SettingsConfigDict(
        # Both files are optional. User-level (~/.tinker/.env) takes priority
        # over any .env in the current directory.
        env_file=(_USER_ENV, _LOCAL_ENV),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── LLM — provider keys (set the one(s) you use) ─────────────────────────
    # LiteLLM picks up keys by name automatically.
    anthropic_api_key: SecretStr | None = Field(None, description="Anthropic direct API key")
    openrouter_api_key: SecretStr | None = Field(None, description="OpenRouter API key")
    openai_api_key: SecretStr | None = Field(None, description="OpenAI API key")
    groq_api_key: SecretStr | None = Field(None, description="Groq API key")
    mistral_api_key: SecretStr | None = Field(None, description="Mistral API key")

    # Model strings — any LiteLLM-compatible value:
    #   anthropic/claude-sonnet-4-6
    #   openrouter/anthropic/claude-opus-4-6
    #   openrouter/openai/gpt-4o
    #   groq/llama-3.1-70b-versatile
    #   ollama/llama3
    default_model: str = Field(
        "anthropic/claude-sonnet-4-6", alias="TINKER_DEFAULT_MODEL"
    )
    deep_rca_model: str = Field(
        "anthropic/claude-opus-4-6", alias="TINKER_DEEP_RCA_MODEL"
    )

    # ── Active backend ────────────────────────────────────────────────────────
    # One of: cloudwatch | gcp | azure | grafana | datadog | elastic | otel
    tinker_backend: str = Field("cloudwatch", alias="TINKER_BACKEND")

    # ── Server ────────────────────────────────────────────────────────────────
    tinker_server_host: str = Field("0.0.0.0", alias="TINKER_SERVER_HOST")
    tinker_server_port: int = Field(8000, alias="TINKER_SERVER_PORT")
    # JSON array of {hash, subject, roles} — see server/auth.py
    tinker_api_keys: str = Field("[]", alias="TINKER_API_KEYS")
    # JWKS URL for JWT validation (optional, for SSO)
    tinker_jwt_jwks_url: str | None = Field(None, alias="TINKER_JWT_JWKS_URL")
    tinker_jwt_audience: str = Field("tinker", alias="TINKER_JWT_AUDIENCE")

    # ── AWS / CloudWatch ──────────────────────────────────────────────────────
    aws_profile: str | None = None
    aws_region: str = "us-east-1"

    # ── GCP ───────────────────────────────────────────────────────────────────
    google_application_credentials: str | None = None
    gcp_project_id: str | None = None

    # ── Azure ─────────────────────────────────────────────────────────────────
    azure_workspace_id: str | None = None        # Log Analytics workspace
    azure_subscription_id: str | None = None
    azure_resource_group: str | None = None
    azure_tenant_id: str | None = None           # only for EnvironmentCredential
    azure_client_id: str | None = None           # only for EnvironmentCredential / pod identity
    azure_client_secret: SecretStr | None = None  # avoid in prod — use Managed Identity

    # ── Grafana Stack (Loki + Prometheus + Tempo) ─────────────────────────────
    grafana_loki_url: str | None = None
    grafana_prometheus_url: str | None = None
    grafana_tempo_url: str | None = None
    grafana_api_key: SecretStr | None = None     # Grafana Cloud API key
    grafana_user: str | None = None              # basic auth user (self-hosted)
    grafana_password: SecretStr | None = None    # basic auth password (self-hosted)
    # Label key used to identify services in Loki stream selectors.
    # Common values: service (default), app, job, service_name, container
    grafana_service_label: str = Field("service", alias="GRAFANA_SERVICE_LABEL")

    # ── Datadog ───────────────────────────────────────────────────────────────
    datadog_api_key: SecretStr | None = None
    datadog_app_key: SecretStr | None = None
    datadog_site: str = "datadoghq.com"

    # ── Elasticsearch / OpenSearch ────────────────────────────────────────────
    elasticsearch_url: str | None = None
    elasticsearch_api_key: SecretStr | None = None

    # ── OTel universal (OpenSearch + Prometheus) ──────────────────────────────
    opensearch_url: str | None = None
    opensearch_api_key: SecretStr | None = None
    prometheus_url: str | None = None
    otel_log_index_pattern: str = "otel-logs-*"

    # ── Slack ─────────────────────────────────────────────────────────────────
    slack_bot_token: SecretStr | None = None
    slack_app_token: SecretStr | None = None
    slack_signing_secret: SecretStr | None = None
    slack_alerts_channel: str = "#incidents"

    # ── GitHub ────────────────────────────────────────────────────────────────
    github_token: SecretStr | None = None
    # Single repo (simple case): org/repo
    github_repo: str | None = None
    # Multiple repos (JSON map of service → org/repo):
    # GITHUB_REPOS='{"payments-api":"acme/payments","auth-service":"acme/auth"}'
    # If set, takes priority over GITHUB_REPO for service-specific lookups.
    github_repos: str = Field("{}", alias="GITHUB_REPOS")

    # ── Local state (sessions, watches) ──────────────────────────────────────
    # Default: ~/.tinker/tinker.db
    tinker_db_path: str | None = Field(None, alias="TINKER_DB_PATH")

    # ── Monitoring ────────────────────────────────────────────────────────────
    poll_interval_seconds: int = Field(60, alias="TINKER_POLL_INTERVAL_SECONDS")
    anomaly_cooldown_minutes: int = Field(30, alias="TINKER_ANOMALY_COOLDOWN_MINUTES")

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = Field("INFO", alias="TINKER_LOG_LEVEL")


# Singleton — import this instead of constructing per-module
settings = TinkerConfig()  # type: ignore[call-arg]

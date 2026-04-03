"""Central configuration loaded from environment variables."""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class TinkerConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── LLM ──────────────────────────────────────────────────────────────────
    anthropic_api_key: SecretStr = Field(..., description="Anthropic API key")
    default_model: str = Field("claude-sonnet-4-6", alias="TINKER_DEFAULT_MODEL")
    deep_rca_model: str = Field("claude-opus-4-6", alias="TINKER_DEEP_RCA_MODEL")

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
    github_repo: str | None = None

    # ── Codebase ──────────────────────────────────────────────────────────────
    tinker_repo_path: str | None = Field(None, alias="TINKER_REPO_PATH")

    # ── Monitoring ────────────────────────────────────────────────────────────
    poll_interval_seconds: int = Field(60, alias="TINKER_POLL_INTERVAL_SECONDS")
    anomaly_cooldown_minutes: int = Field(30, alias="TINKER_ANOMALY_COOLDOWN_MINUTES")

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = Field("INFO", alias="TINKER_LOG_LEVEL")


# Singleton — import this instead of constructing per-module
settings = TinkerConfig()  # type: ignore[call-arg]

"""Environment-variable pass-through for secrets that cloud SDKs and LiteLLM
read directly from os.environ.

This is intentionally small. Everything else lives in ~/.tinkr/config.toml.

What belongs here
-----------------
  LLM provider API keys  — LiteLLM reads ANTHROPIC_API_KEY etc. from env
  AWS standard env vars  — boto3 also reads AWS_PROFILE / AWS_DEFAULT_REGION
  GCP credential path    — google-auth reads GOOGLE_APPLICATION_CREDENTIALS
  Azure identity env vars — Azure SDK EnvironmentCredential reads these by name
  TINKR_DB_PATH         — optional SQLite path override

What does NOT belong here
-------------------------
  Model names, server host/port, Slack/GitHub tokens, backend URLs, API keys
  for Grafana/Datadog/Elastic/OTel — all of those live in config.toml.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_tinker_dir = Path.home() / ".tinkr"
_tinker_dir.mkdir(parents=True, exist_ok=True)

_USER_ENV = str(_tinker_dir / ".env")
_LOCAL_ENV = ".env"


class TinkerConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_USER_ENV, _LOCAL_ENV),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── LLM provider keys — picked up automatically by LiteLLM ───────────────
    anthropic_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    mistral_api_key: SecretStr | None = None

    # ── AWS — boto3 also reads these standard env vars directly ──────────────
    aws_profile: str | None = None
    aws_region: str = "us-east-1"

    # ── GCP — google-auth reads GOOGLE_APPLICATION_CREDENTIALS by convention ─
    google_application_credentials: str | None = None

    # ── Azure — DefaultAzureCredential / EnvironmentCredential reads these ───
    azure_tenant_id: str | None = None
    azure_client_id: str | None = None
    azure_client_secret: SecretStr | None = None

    # ── Internal ──────────────────────────────────────────────────────────────
    tinker_db_path: str | None = Field(None, alias="TINKR_DB_PATH")


# Singleton — import this instead of constructing per-module
settings = TinkerConfig()  # type: ignore[call-arg]

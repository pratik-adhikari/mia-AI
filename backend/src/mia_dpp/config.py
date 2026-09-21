"""Validated environment configuration."""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_ROOT = REPOSITORY_ROOT / ".mia-data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPOSITORY_ROOT / ".env.local",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openrouter_api_key: SecretStr | None = None
    clerk_secret_key: SecretStr | None = None
    clerk_jwt_key: SecretStr | None = None
    clerk_authorized_parties: str = Field(
        default="http://localhost:3000",
        validation_alias="CLERK_AUTHORIZED_PARTIES",
    )
    workflow_secret: SecretStr | None = Field(default=None, validation_alias="MIA_WORKFLOW_SECRET")
    agent_model: str = Field(
        default="deepseek/deepseek-v3.2",
        validation_alias="MIA_AGENT_MODEL",
    )
    database_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MIA_DATABASE_URL", "DATABASE_URL"),
    )
    catalogue_path: Path = Field(
        default=DEFAULT_DATA_ROOT / "catalogue.sqlite3",
        validation_alias="MIA_CATALOGUE_PATH",
    )
    blob_read_write_token: SecretStr | None = Field(
        default=None,
        validation_alias="BLOB_READ_WRITE_TOKEN",
    )
    blob_store_id: str | None = Field(default=None, validation_alias="BLOB_STORE_ID")
    vercel_environment: str | None = Field(default=None, validation_alias="VERCEL_ENV")
    thread_store_path: Path = Field(
        default=DEFAULT_DATA_ROOT / "mia-agent.sqlite3",
        validation_alias="MIA_THREAD_STORE_PATH",
    )
    workspace_root: Path = Field(
        default=DEFAULT_DATA_ROOT / "workspaces",
        validation_alias="MIA_WORKSPACE_ROOT",
    )
    standards_root: Path = Field(
        default=REPOSITORY_ROOT / "standards" / "idta-submodel-templates",
        validation_alias="MIA_STANDARDS_ROOT",
    )
    cors_origin_regex: str = Field(
        default=r"https?://(127[.]0[.]0[.]1|localhost):[0-9]+",
        validation_alias="MIA_CORS_ORIGIN_REGEX",
    )

    @property
    def authorized_parties(self) -> list[str]:
        """Return the configured Clerk token origins without accepting empty entries."""

        return [item.strip() for item in self.clerk_authorized_parties.split(",") if item.strip()]

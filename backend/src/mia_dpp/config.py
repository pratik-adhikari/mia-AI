"""Validated environment configuration."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPOSITORY_ROOT / ".env.local",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openrouter_api_key: SecretStr | None = None
    agent_model: str = Field(
        default="deepseek/deepseek-v3.2",
        validation_alias="MIA_AGENT_MODEL",
    )
    thread_store_path: Path = Field(
        default=Path("/tmp/mia-agent.sqlite3"),
        validation_alias="MIA_THREAD_STORE_PATH",
    )
    workspace_root: Path = Field(
        default=Path("/tmp/mia-workspaces"),
        validation_alias="MIA_WORKSPACE_ROOT",
    )
    web_schema_root: Path = Field(
        default=Path("/tmp/mia-web-schemas"),
        validation_alias="MIA_WEB_SCHEMA_ROOT",
    )
    standards_root: Path = Field(
        default=REPOSITORY_ROOT / "standards" / "idta-submodel-templates",
        validation_alias="MIA_STANDARDS_ROOT",
    )
    cors_origin_regex: str = Field(
        default=r"https?://(127[.]0[.]0[.]1|localhost):[0-9]+",
        validation_alias="MIA_CORS_ORIGIN_REGEX",
    )

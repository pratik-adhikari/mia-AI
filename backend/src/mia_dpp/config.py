"""Validated environment configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import AliasChoices, Field, PrivateAttr, SecretStr
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
    config_path: Path = Field(
        default=REPOSITORY_ROOT / "config.json",
        validation_alias="MIA_CONFIG_PATH",
    )
    clerk_secret_key: SecretStr | None = None
    clerk_jwt_key: SecretStr | None = None
    clerk_authorized_parties: str = Field(
        default="http://localhost:3000",
        validation_alias="CLERK_AUTHORIZED_PARTIES",
    )
    workflow_secret: SecretStr | None = Field(default=None, validation_alias="MIA_WORKFLOW_SECRET")
    local_mode: bool = Field(default=False, validation_alias="MIA_LOCAL_MODE")
    agent_server_url: str | None = Field(default=None, validation_alias="MIA_AGENT_SERVER_URL")
    agent_model: str = Field(
        default="deepseek/deepseek-v3.2",
        validation_alias="MIA_AGENT_MODEL",
    )
    architecture_id: str = Field(
        default="graph-v1",
        validation_alias="MIA_ARCHITECTURE",
    )
    jev_shadow_enabled: bool = Field(
        default=False,
        validation_alias="MIA_JEV_SHADOW_ENABLED",
    )
    jev_mapping_enabled: bool = Field(
        default=False,
        validation_alias="MIA_JEV_MAPPING_ENABLED",
    )
    jev_model: str = Field(
        default="typesafe/jev-1.13",
        validation_alias="MIA_JEV_MODEL",
    )
    jev_max_concurrency: int = Field(
        default=8,
        ge=1,
        le=64,
        validation_alias="MIA_JEV_MAX_CONCURRENCY",
    )
    jev_auto_min_selected_probability: float = Field(
        default=0.80,
        ge=0.0,
        le=1.0,
        validation_alias="MIA_JEV_AUTO_MIN_SELECTED_PROBABILITY",
    )
    jev_auto_min_margin: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        validation_alias="MIA_JEV_AUTO_MIN_MARGIN",
    )
    jev_auto_max_runner_up_ratio: float = Field(
        default=0.12,
        ge=0.0,
        le=1.0,
        validation_alias="MIA_JEV_AUTO_MAX_RUNNER_UP_RATIO",
    )
    jev_auto_max_entropy: float = Field(
        default=0.45,
        ge=0.0,
        le=1.0,
        validation_alias="MIA_JEV_AUTO_MAX_ENTROPY",
    )
    jev_optional_min_selected_probability: float = Field(
        default=0.70,
        ge=0.0,
        le=1.0,
        validation_alias="MIA_JEV_OPTIONAL_MIN_SELECTED_PROBABILITY",
    )
    jev_optional_min_margin: float = Field(
        default=0.45,
        ge=0.0,
        le=1.0,
        validation_alias="MIA_JEV_OPTIONAL_MIN_MARGIN",
    )
    jev_optional_max_runner_up_ratio: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        validation_alias="MIA_JEV_OPTIONAL_MAX_RUNNER_UP_RATIO",
    )
    jev_optional_max_entropy: float = Field(
        default=0.65,
        ge=0.0,
        le=1.0,
        validation_alias="MIA_JEV_OPTIONAL_MAX_ENTROPY",
    )
    jev_grouping_max_groups: int = Field(
        default=200,
        ge=1,
        le=253,
        validation_alias="MIA_JEV_GROUPING_MAX_GROUPS",
    )
    eclass_shadow_enabled: bool = Field(
        default=False,
        validation_alias="MIA_ECLASS_SHADOW_ENABLED",
    )
    eclass_provider_mode: Literal["local", "api"] = Field(
        default="api",
        validation_alias="MIA_ECLASS_PROVIDER",
    )
    eclass_certificate_file: Path | None = Field(
        default=None,
        validation_alias="MIA_ECLASS_CERTIFICATE_FILE",
    )
    eclass_xml_dictionary_zips: str | None = Field(
        default=None,
        validation_alias="MIA_ECLASS_XML_DICTIONARY_ZIPS",
    )
    eclass_xml_language: str = Field(
        default="en",
        validation_alias="MIA_ECLASS_XML_LANGUAGE",
    )
    eclass_key_file: Path | None = Field(
        default=None,
        validation_alias="MIA_ECLASS_KEY_FILE",
    )
    eclass_json_base_url: str = Field(
        default="https://eclass-cdp.com/jsonapi/v2",
        validation_alias="MIA_ECLASS_JSON_BASE_URL",
    )
    eclass_search_parameter: str = Field(
        default="preferredName",
        validation_alias="MIA_ECLASS_SEARCH_PARAMETER",
    )
    eclass_candidate_limit: int = Field(
        default=12,
        ge=1,
        le=64,
        validation_alias="MIA_ECLASS_CANDIDATE_LIMIT",
    )
    semantic_promotion_enabled: bool = Field(
        default=False,
        validation_alias="MIA_SEMANTIC_PROMOTION_ENABLED",
    )
    mia_database_url: str | None = Field(
        default=None,
        validation_alias="MIA_DATABASE_URL",
    )
    supabase_postgres_url_non_pooling: str | None = Field(
        default=None,
        validation_alias="SUPABASE_POSTGRES_URL_NON_POOLING",
    )
    postgres_url_non_pooling: str | None = Field(
        default=None,
        validation_alias="POSTGRES_URL_NON_POOLING",
    )
    supabase_postgres_url: str | None = Field(
        default=None,
        validation_alias="SUPABASE_POSTGRES_URL",
    )
    postgres_url: str | None = Field(
        default=None,
        validation_alias="POSTGRES_URL",
    )
    generic_database_url: str | None = Field(
        default=None,
        validation_alias="DATABASE_URL",
    )
    catalogue_path: Path = Field(
        default=DEFAULT_DATA_ROOT / "catalogue.sqlite3",
        validation_alias="MIA_CATALOGUE_PATH",
    )
    blob_read_write_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "BLOB_READ_WRITE_TOKEN",
            "VERCEL_BLOB_READ_WRITE_TOKEN",
        ),
    )
    blob_store_id: str | None = Field(default=None, validation_alias="BLOB_STORE_ID")
    vercel_environment: str | None = Field(default=None, validation_alias="VERCEL_ENV")
    vercel_url: str | None = Field(default=None, validation_alias="VERCEL_URL")
    vercel_branch_url: str | None = Field(default=None, validation_alias="VERCEL_BRANCH_URL")
    vercel_project_production_url: str | None = Field(
        default=None,
        validation_alias="VERCEL_PROJECT_PRODUCTION_URL",
    )
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
    _authentication_enabled: bool = PrivateAttr(default=True)

    def model_post_init(self, context: Any) -> None:
        if not self.local_mode or self.vercel_environment:
            return
        try:
            config = json.loads(self.config_path.read_text(encoding="utf-8"))
            auth_config = config.get("auth", {}) if isinstance(config, dict) else {}
            self._authentication_enabled = not (
                isinstance(auth_config, dict) and auth_config.get("enabled") is False
            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            self._authentication_enabled = True

    @property
    def authentication_enabled(self) -> bool:
        return self._authentication_enabled

    @property
    def database_url(self) -> str | None:
        """Return a psycopg-compatible PostgreSQL URL.

        Explicit local mode always uses the configured SQLite catalogue/checkpoint paths even if
        stale Vercel/Supabase variables remain in .env.local.

        Vercel and Supabase may expose pooled URLs with metadata query parameters
        such as supa that framework integrations understand but libpq/psycopg does
        not. Prefer the session/non-pooling URL when an explicit override carries
        those pooler-only parameters, then remove known client-only parameters as
        a final compatibility guard.
        """

        if self.local_mode:
            return None

        session_url = self.supabase_postgres_url_non_pooling or self.postgres_url_non_pooling
        explicit = self.mia_database_url
        if explicit and self._has_client_only_database_params(explicit) and session_url:
            candidate: str | None = session_url
        else:
            candidate = (
                explicit
                or session_url
                or self.supabase_postgres_url
                or self.postgres_url
                or self.generic_database_url
            )
        return self._normalize_database_url(candidate) if candidate else None

    @staticmethod
    def _has_client_only_database_params(url: str) -> bool:
        return any(
            key in {"supa", "pgbouncer", "connection_limit"}
            for key, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)
        )

    @staticmethod
    def _normalize_database_url(url: str) -> str:
        parsed = urlsplit(url)
        query = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key not in {"supa", "pgbouncer", "connection_limit"}
        ]
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(query),
                parsed.fragment,
            )
        )

    @property
    def authorized_parties(self) -> list[str]:
        """Return the exact frontend origins allowed to mint Clerk session tokens.

        Explicit configuration remains authoritative, while Vercel's system URLs are
        added so Preview deployments authenticate correctly without hard-coding a
        deployment-specific hostname after every redeploy.
        """

        parties = [
            item.strip() for item in self.clerk_authorized_parties.split(",") if item.strip()
        ]
        for host in (
            self.vercel_url,
            self.vercel_branch_url,
            self.vercel_project_production_url,
        ):
            if not host:
                continue
            origin = host if host.startswith(("http://", "https://")) else f"https://{host}"
            if origin not in parties:
                parties.append(origin)
        return parties

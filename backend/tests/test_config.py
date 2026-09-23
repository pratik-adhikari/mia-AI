from mia_dpp.config import Settings


def test_vercel_origins_are_added_to_clerk_authorized_parties() -> None:
    settings = Settings(
        _env_file=None,
        CLERK_AUTHORIZED_PARTIES="http://localhost:3000",
        VERCEL_URL="mia-preview-abc.vercel.app",
        VERCEL_BRANCH_URL="mia-git-integration-clean-snapshots.vercel.app",
        VERCEL_PROJECT_PRODUCTION_URL="mia.example.com",
    )

    assert settings.authorized_parties == [
        "http://localhost:3000",
        "https://mia-preview-abc.vercel.app",
        "https://mia-git-integration-clean-snapshots.vercel.app",
        "https://mia.example.com",
    ]


def test_supabase_non_pooling_url_wins_over_pooled_url() -> None:
    settings = Settings(
        _env_file=None,
        SUPABASE_POSTGRES_URL="postgresql://pool.example.test/postgres?supa=base-pooler.x",
        SUPABASE_POSTGRES_URL_NON_POOLING="postgresql://session.example.test/postgres",
    )

    assert settings.database_url == "postgresql://session.example.test/postgres"


def test_mia_pooled_override_falls_back_to_supabase_session_url() -> None:
    settings = Settings(
        _env_file=None,
        MIA_DATABASE_URL="postgresql://pool.example.test/postgres?supa=base-pooler.x",
        SUPABASE_POSTGRES_URL_NON_POOLING="postgresql://session.example.test/postgres?sslmode=require",
    )

    assert settings.database_url == "postgresql://session.example.test/postgres?sslmode=require"


def test_database_url_removes_client_only_pooler_parameters() -> None:
    settings = Settings(
        _env_file=None,
        MIA_DATABASE_URL=(
            "postgresql://pool.example.test/postgres"
            "?sslmode=require&supa=base-pooler.x&pgbouncer=true&connection_limit=1"
        ),
    )

    assert settings.database_url == "postgresql://pool.example.test/postgres?sslmode=require"


def test_vercel_blob_token_alias_is_accepted() -> None:
    settings = Settings(
        _env_file=None,
        VERCEL_BLOB_READ_WRITE_TOKEN="blob-test-token",
    )

    assert settings.blob_read_write_token is not None
    assert settings.blob_read_write_token.get_secret_value() == "blob-test-token"


def test_local_mode_ignores_stale_remote_database_urls() -> None:
    settings = Settings(
        _env_file=None,
        MIA_LOCAL_MODE=True,
        MIA_DATABASE_URL="postgresql://remote.example.test/postgres",
        SUPABASE_POSTGRES_URL_NON_POOLING="postgresql://session.example.test/postgres",
    )

    assert settings.database_url is None


def test_local_config_can_disable_authentication(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text('{"auth":{"enabled":false}}', encoding="utf-8")

    settings = Settings(_env_file=None, MIA_LOCAL_MODE=True, MIA_CONFIG_PATH=config_path)

    assert settings.authentication_enabled is False


def test_authentication_cannot_be_disabled_outside_local_mode(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text('{"auth":{"enabled":false}}', encoding="utf-8")

    settings = Settings(_env_file=None, MIA_CONFIG_PATH=config_path)

    assert settings.authentication_enabled is True


def test_authentication_cannot_be_disabled_in_vercel(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text('{"auth":{"enabled":false}}', encoding="utf-8")

    settings = Settings(
        _env_file=None,
        MIA_LOCAL_MODE=True,
        MIA_CONFIG_PATH=config_path,
        VERCEL_ENV="production",
    )

    assert settings.authentication_enabled is True


def test_missing_or_invalid_auth_config_defaults_to_enabled(tmp_path) -> None:
    missing = Settings(
        _env_file=None,
        MIA_LOCAL_MODE=True,
        MIA_CONFIG_PATH=tmp_path / "missing.json",
    )
    malformed_path = tmp_path / "invalid.json"
    malformed_path.write_text("{invalid", encoding="utf-8")
    malformed = Settings(
        _env_file=None,
        MIA_LOCAL_MODE=True,
        MIA_CONFIG_PATH=malformed_path,
    )

    assert missing.authentication_enabled is True
    assert malformed.authentication_enabled is True


def test_explicit_auth_config_can_keep_local_auth_enabled(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text('{"auth":{"enabled":true}}', encoding="utf-8")

    settings = Settings(_env_file=None, MIA_LOCAL_MODE=True, MIA_CONFIG_PATH=config_path)

    assert settings.authentication_enabled is True

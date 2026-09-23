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

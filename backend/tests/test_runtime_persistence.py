from pathlib import Path

import pytest

from mia_dpp.config import Settings
from mia_dpp.runtime.factory import create_artifact_store, create_catalogue
from mia_dpp.storage.local import LocalArtifactStore


def test_local_runtime_uses_sqlite_and_filesystem(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        MIA_CATALOGUE_PATH=tmp_path / "catalogue.sqlite3",
        MIA_WORKSPACE_ROOT=tmp_path / "artifacts",
    )
    assert create_catalogue(settings).backend == "sqlite"
    assert isinstance(create_artifact_store(settings), LocalArtifactStore)


def test_vercel_refuses_ephemeral_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, VERCEL_ENV="production")
    with pytest.raises(RuntimeError, match="MIA_DATABASE_URL"):
        create_artifact_store(settings)


def test_supabase_vercel_url_is_accepted_as_database_url() -> None:
    settings = Settings(
        _env_file=None,
        SUPABASE_POSTGRES_URL="postgresql://user:password@example.test/postgres",
    )
    assert settings.database_url == "postgresql://user:password@example.test/postgres"


def test_vercel_rejects_blob_store_id_without_token() -> None:
    settings = Settings(
        _env_file=None,
        VERCEL_ENV="preview",
        MIA_DATABASE_URL="postgresql://user:password@example.test/postgres",
        BLOB_STORE_ID="store_123",
    )

    with pytest.raises(RuntimeError, match="BLOB_STORE_ID alone is not authentication"):
        create_artifact_store(settings)


def test_background_job_can_checkpoint_and_requeue_between_batches(tmp_path: Path) -> None:
    from mia_dpp.domain.product import BackgroundJobStatus
    from mia_dpp.persistence.catalogue import ProductCatalogue

    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    product, _ = catalogue.get_or_create_product("https://example.com/resumable")
    thread_id = "thread-resumable"
    catalogue.get_or_create_thread(thread_id, "local-development")
    run = catalogue.start_run(product.id, thread_id)
    job = catalogue.create_background_job(
        user_id="local-development",
        thread_id=thread_id,
        product_id=product.id,
        run_id=run.id,
        metadata={"nextSourceIndex": 0, "iteration": 0},
    )

    claimed = catalogue.claim_background_job(job.id, user_id="local-development")
    assert claimed is not None
    assert claimed.status is BackgroundJobStatus.RUNNING

    queued = catalogue.requeue_background_job(
        job.id,
        user_id="local-development",
        metadata={"nextSourceIndex": 2, "iteration": 1},
    )
    assert queued.status is BackgroundJobStatus.QUEUED
    assert queued.metadata["nextSourceIndex"] == 2

    resumed = catalogue.claim_background_job(job.id, user_id="local-development")
    assert resumed is not None
    assert resumed.status is BackgroundJobStatus.RUNNING
    assert resumed.metadata["nextSourceIndex"] == 2


def test_worker_selects_only_queued_background_jobs(tmp_path: Path) -> None:
    from mia_dpp.domain.product import BackgroundJobStatus
    from mia_dpp.persistence.catalogue import ProductCatalogue

    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    product, _ = catalogue.get_or_create_product("https://example.com/worker")
    thread_id = "thread-worker"
    catalogue.get_or_create_thread(thread_id, "local-development")
    run = catalogue.start_run(product.id, thread_id)
    job = catalogue.create_background_job(
        user_id="local-development",
        thread_id=thread_id,
        product_id=product.id,
        run_id=run.id,
    )

    assert catalogue.next_queued_background_job() == job
    claimed = catalogue.claim_background_job(job.id, user_id="local-development")
    assert claimed is not None
    assert claimed.status is BackgroundJobStatus.RUNNING
    assert catalogue.next_queued_background_job() is None


def test_local_mode_forces_sqlite_and_filesystem_even_with_vercel_variables(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        MIA_LOCAL_MODE=True,
        VERCEL_ENV="preview",
        MIA_DATABASE_URL="postgresql://remote.example.test/postgres",
        BLOB_READ_WRITE_TOKEN="blob-token",
        MIA_CATALOGUE_PATH=tmp_path / "catalogue.sqlite3",
        MIA_WORKSPACE_ROOT=tmp_path / "artifacts",
    )

    assert create_catalogue(settings).backend == "sqlite"
    assert isinstance(create_artifact_store(settings), LocalArtifactStore)

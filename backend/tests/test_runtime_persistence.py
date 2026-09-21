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

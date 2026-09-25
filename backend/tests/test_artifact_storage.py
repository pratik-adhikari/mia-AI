from pathlib import Path

from mia_dpp.storage.local import LocalArtifactStore
from mia_dpp.storage.models import StoredArtifact
from mia_dpp.storage.vercel_blob import VercelBlobArtifactStore


def test_local_artifact_store_round_trips_bytes(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    artifact = store.put(
        "evidence/source.json",
        b'{"voltage":"24 V"}',
        content_type="application/json",
        product_id="product-one",
        run_id="run-one",
    )
    assert artifact.product_id == "product-one"
    assert artifact.run_id == "run-one"
    assert store.get(artifact) == b'{"voltage":"24 V"}'


def test_vercel_blob_reads_private_artifacts_with_private_access() -> None:
    class PrivateBlobClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def get(self, url: str, *, access: str) -> bytes:
            self.calls.append((url, access))
            return b"payload"

    client = PrivateBlobClient()
    store = VercelBlobArtifactStore(client=client)
    artifact = StoredArtifact(
        id="artifact-test",
        key="evidence/source.json",
        content_type="application/json",
        sha256="0" * 64,
        size=7,
        storage_uri="https://example.private.blob.vercel-storage.com/evidence/source.json",
    )

    assert store.get(artifact) == b"payload"
    assert client.calls == [(artifact.storage_uri, "private")]


def test_vercel_exists_returns_false_only_for_not_found() -> None:
    class NotFoundError(RuntimeError):
        status_code = 404

    class MissingClient:
        def get(self, url: str, *, access: str) -> bytes:
            raise NotFoundError("missing")

    artifact = StoredArtifact(
        id="artifact-missing",
        key="evidence/source.json",
        content_type="application/json",
        sha256="0" * 64,
        size=7,
        storage_uri="https://example.private.blob.vercel-storage.com/missing.json",
    )

    assert VercelBlobArtifactStore(client=MissingClient()).exists(artifact) is False


def test_vercel_exists_propagates_backend_failure() -> None:
    class UnavailableError(RuntimeError):
        status_code = 503

    class BrokenClient:
        def get(self, url: str, *, access: str) -> bytes:
            raise UnavailableError("service unavailable")

    artifact = StoredArtifact(
        id="artifact-unavailable",
        key="evidence/source.json",
        content_type="application/json",
        sha256="0" * 64,
        size=7,
        storage_uri="https://example.private.blob.vercel-storage.com/source.json",
    )

    import pytest

    with pytest.raises(UnavailableError):
        VercelBlobArtifactStore(client=BrokenClient()).exists(artifact)

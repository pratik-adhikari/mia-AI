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

from pathlib import Path

from mia_dpp.storage.local import LocalArtifactStore


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

"""Legacy workspace API views backed by the new durable stores."""

from pathlib import Path

from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.persistence.workspace import WorkspaceView
from mia_dpp.storage.local import LocalArtifactStore


def test_workspace_view_reads_thread_artifacts_and_events(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    storage = LocalArtifactStore(tmp_path / "artifacts")
    product, _ = catalogue.get_or_create_product("https://example.com/product/42")
    run = catalogue.start_run(product.id, "thread-workspace")
    artifact = storage.put(
        "evidence/product.json",
        b'{"name":"Example"}',
        content_type="application/json",
        product_id=product.id,
        run_id=run.id,
    )
    catalogue.register_artifact(artifact)
    catalogue.add_event(run.id, "source.extracted", "Stored one evidence artifact.")

    workspace = WorkspaceView(catalogue, storage)
    listed = workspace.list_artifacts("thread-workspace")
    assert [item.id for item in listed] == [artifact.id]
    assert workspace.read_artifact("thread-workspace", artifact.id)[1] == b'{"name":"Example"}'
    assert workspace.list_events("thread-workspace")[0].event_type == "source.extracted"
    assert workspace.combined_export("thread-workspace")["jsonArtifacts"][artifact.id] == {
        "name": "Example"
    }


def test_workspace_view_isolates_threads(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    storage = LocalArtifactStore(tmp_path / "artifacts")
    first, _ = catalogue.get_or_create_product("https://example.com/first")
    second, _ = catalogue.get_or_create_product("https://example.com/second")
    first_run = catalogue.start_run(first.id, "thread-first")
    second_run = catalogue.start_run(second.id, "thread-second")
    for product, run, value in ((first, first_run, b"first"), (second, second_run, b"second")):
        artifact = storage.put(
            "evidence/value.txt",
            value,
            content_type="text/plain",
            product_id=product.id,
            run_id=run.id,
        )
        catalogue.register_artifact(artifact)

    workspace = WorkspaceView(catalogue, storage)
    assert [item.product_id for item in workspace.list_artifacts("thread-first")] == [first.id]
    assert [item.product_id for item in workspace.list_artifacts("thread-second")] == [second.id]

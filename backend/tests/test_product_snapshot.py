from dataclasses import dataclass

from pydantic import BaseModel

from mia_dpp.workflow.product_snapshot import model_fingerprint, semantic_mapper_fingerprint


class FixtureModel(BaseModel):
    alpha: int
    beta: str


def test_model_fingerprint_is_stable_and_changes_with_content() -> None:
    first = model_fingerprint(FixtureModel(alpha=1, beta="x"))
    same = model_fingerprint(FixtureModel(alpha=1, beta="x"))
    changed = model_fingerprint(FixtureModel(alpha=2, beta="x"))

    assert first == same
    assert first != changed


@dataclass
class FixtureMapper:
    pass


def test_semantic_mapper_fingerprint_identifies_mapper_implementation() -> None:
    assert semantic_mapper_fingerprint(FixtureMapper()) == semantic_mapper_fingerprint(FixtureMapper())
    assert semantic_mapper_fingerprint(None) is None



def test_workflow_snapshot_update_rejects_stale_graph_state(tmp_path) -> None:
    from types import SimpleNamespace

    import pytest

    from mia_dpp.domain.product_work import ProductWorkSnapshot, ProductWorkStage
    from mia_dpp.persistence.catalogue import ProductCatalogue, ProductSnapshotConflict
    from mia_dpp.workflow.product_snapshot import update_product_snapshot
    from mia_dpp.workflow.workspace import RunWorkspace

    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    catalogue.get_or_create_thread("thread-causal", "user-a")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/causal",
        user_id="user-a",
    )
    run = catalogue.start_run(product.id, "thread-causal", user_id="user-a")
    first = catalogue.save_product_work_snapshot(
        ProductWorkSnapshot(
            id="snapshot-causal",
            user_id="user-a",
            product_id=product.id,
            run_id=run.id,
            thread_id=run.thread_id,
            workflow_stage=ProductWorkStage.EVIDENCE,
        )
    )
    newer = catalogue.save_product_work_snapshot(
        first.model_copy(update={"workflow_stage": ProductWorkStage.MAPPING}),
        expected_version=first.version,
    )
    work = RunWorkspace(
        {
            "user_id": "user-a",
            "thread_id": run.thread_id,
            "product_id": product.id,
            "run_id": run.id,
            "product_snapshot_version": first.version,
        },
        SimpleNamespace(catalogue=catalogue),
    )

    with pytest.raises(ProductSnapshotConflict):
        update_product_snapshot(work, ProductWorkStage.COVERAGE)

    assert catalogue.get_product_work_snapshot(product.id, user_id="user-a") == newer

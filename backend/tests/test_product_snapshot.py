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
    assert semantic_mapper_fingerprint(FixtureMapper()) == semantic_mapper_fingerprint(
        FixtureMapper()
    )
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


def test_recovered_generation_fences_old_snapshot_and_run_mutations(tmp_path) -> None:
    from datetime import UTC, datetime, timedelta

    import pytest

    from mia_dpp.domain.product import RunStatus
    from mia_dpp.domain.product_work import ProductWorkSnapshot, ProductWorkStage
    from mia_dpp.persistence.catalogue import ProductCatalogue

    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    catalogue.get_or_create_thread("thread-fence", "user-a")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/fence",
        user_id="user-a",
    )
    old = catalogue.start_run(product.id, "thread-fence", user_id="user-a")
    first = catalogue.save_product_work_snapshot(
        ProductWorkSnapshot(
            id="snapshot-fence",
            user_id="user-a",
            product_id=product.id,
            run_id=old.id,
            thread_id=old.thread_id,
            workflow_stage=ProductWorkStage.EVIDENCE,
        )
    )
    expired = old.model_copy(
        update={"execution_lease_expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    catalogue._execute(
        "UPDATE runs SET payload=? WHERE id=?",
        (expired.model_dump_json(), old.id),
    )
    replacement = catalogue.claim_product_restart(
        user_id="user-a",
        product_id=product.id,
        expected_run_id=old.id,
        expected_generation=0,
        reason="fixture recovery",
        refresh_requested=False,
        require_expired_lease=True,
    )
    stale_snapshot = first.model_copy(update={"workflow_stage": ProductWorkStage.MAPPING})

    with pytest.raises(RuntimeError, match="workflow generation"):
        catalogue.save_product_work_snapshot(
            stale_snapshot,
            expected_version=first.version,
        )
    with pytest.raises(RuntimeError, match="workflow generation"):
        catalogue.finish_run(old.id, RunStatus.COMPLETED)

    assert catalogue.get_run(replacement.id).status is RunStatus.RUNNING
    assert catalogue.get_product_work_snapshot(product.id, user_id="user-a") == first


def test_stale_workspace_cannot_start_after_generation_advances(tmp_path) -> None:
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    import pytest

    from mia_dpp.persistence.catalogue import ProductCatalogue
    from mia_dpp.workflow.workspace import RunWorkspace

    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    catalogue.get_or_create_thread("thread-workspace-fence", "user-a")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/workspace-fence",
        user_id="user-a",
    )
    old = catalogue.start_run(
        product.id,
        "thread-workspace-fence",
        user_id="user-a",
    )
    expired = old.model_copy(
        update={"execution_lease_expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    catalogue._execute(
        "UPDATE runs SET payload=? WHERE id=?",
        (expired.model_dump_json(), old.id),
    )
    catalogue.claim_product_restart(
        user_id="user-a",
        product_id=product.id,
        expected_run_id=old.id,
        expected_generation=0,
        reason="fixture recovery",
        refresh_requested=False,
        require_expired_lease=True,
    )

    with pytest.raises(RuntimeError, match="workflow generation"):
        RunWorkspace(
            {
                "user_id": "user-a",
                "thread_id": old.thread_id,
                "product_id": product.id,
                "run_id": old.id,
            },
            SimpleNamespace(catalogue=catalogue),
        )

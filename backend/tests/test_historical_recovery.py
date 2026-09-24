from pathlib import Path

from mia_dpp.domain.product import RunStatus
from mia_dpp.domain.product_work import (
    HumanReviewAction,
    HumanReviewRecord,
    ProductWorkSnapshot,
    ProductWorkStage,
    ReuseMode,
)
from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.services.product_reuse import ProductReuseService


def test_failed_build_continues_from_reviewed_saved_work_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "catalogue.sqlite3"
    catalogue = ProductCatalogue(path)
    catalogue.get_or_create_thread("thread-failed", "user-a")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/failed-product",
        user_id="user-a",
    )
    run = catalogue.start_run(product.id, "thread-failed", user_id="user-a")
    catalogue.finish_run(
        run.id,
        RunStatus.FAILED,
        error="AAS validation did not produce a deployable artifact",
    )
    snapshot = catalogue.save_product_work_snapshot(
        ProductWorkSnapshot(
            id="snapshot-failed",
            user_id="user-a",
            product_id=product.id,
            run_id=run.id,
            thread_id=run.thread_id,
            workflow_stage=ProductWorkStage.FAILED,
            evidence_artifact_id="artifact-evidence",
            reviewed_mapping_artifact_id="artifact-reviewed-mapping",
            coverage_artifact_id="artifact-coverage",
            evidence_fingerprint="evidence-v1",
            reviewed_evidence_fingerprint="evidence-v1",
            target_fingerprint="targets-v1",
            reviewed_target_fingerprint="targets-v1",
            last_error="validation failed",
        )
    )

    restarted = ProductCatalogue(path)
    restored = restarted.get_product_work_snapshot(product.id, user_id="user-a")
    decision = ProductReuseService(restarted).decide(
        product.id,
        user_id="user-a",
        refresh_requested=False,
    )

    assert restored == snapshot
    assert decision.mode is ReuseMode.CONTINUE_SAVED_WORK
    assert decision.evidence_artifact_id == "artifact-evidence"
    assert decision.reviewed_mapping_artifact_id == "artifact-reviewed-mapping"


def test_deleting_chat_does_not_delete_snapshot_or_human_audit(tmp_path: Path) -> None:
    path = tmp_path / "catalogue.sqlite3"
    catalogue = ProductCatalogue(path)
    catalogue.get_or_create_thread("thread-delete-recovery", "user-a")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/delete-recovery",
        user_id="user-a",
    )
    run = catalogue.start_run(product.id, "thread-delete-recovery", user_id="user-a")
    snapshot = catalogue.save_product_work_snapshot(
        ProductWorkSnapshot(
            id="snapshot-delete-recovery",
            user_id="user-a",
            product_id=product.id,
            run_id=run.id,
            thread_id=run.thread_id,
            workflow_stage=ProductWorkStage.HUMAN_INPUT,
            evidence_artifact_id="artifact-evidence",
            reviewed_mapping_artifact_id="artifact-mapping",
        )
    )
    audit = HumanReviewRecord(
        id="review-delete-recovery",
        user_id="user-a",
        product_id=product.id,
        run_id=run.id,
        thread_id=run.thread_id,
        action=HumanReviewAction.SUPPLIED_DUMMY,
        actor_name="Pratik",
        final_requirement_id="req-missing",
        value_kind="dummy",
    )
    catalogue.add_human_review(audit)

    catalogue.delete_thread("thread-delete-recovery", user_id="user-a")

    assert catalogue.list_threads("user-a") == ()
    assert catalogue.get_product_work_snapshot(product.id, user_id="user-a") == snapshot
    assert catalogue.list_human_reviews(product.id, user_id="user-a") == (audit,)


def test_refresh_explicitly_ignores_failed_saved_state(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    catalogue.get_or_create_thread("thread-refresh-failed", "user-a")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/refresh-failed",
        user_id="user-a",
    )

    decision = ProductReuseService(catalogue).decide(
        product.id,
        user_id="user-a",
        refresh_requested=True,
    )

    assert decision.mode is ReuseMode.REFRESH_SOURCES

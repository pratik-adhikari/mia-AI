from pathlib import Path

from mia_dpp.domain.product import RunStatus
from mia_dpp.domain.product_work import ProductWorkSnapshot, ProductWorkStage, ReuseMode
from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.services.product_reuse import ProductReuseService


def test_reuse_service_prefers_snapshot_before_legacy_artifact_scan(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    catalogue.get_or_create_thread("thread-reuse", "user-a")
    product, _ = catalogue.get_or_create_product("https://example.com/reuse", user_id="user-a")
    run = catalogue.start_run(product.id, "thread-reuse", user_id="user-a")
    catalogue.save_product_work_snapshot(
        ProductWorkSnapshot(
            id="snapshot-reuse",
            user_id="user-a",
            product_id=product.id,
            run_id=run.id,
            thread_id=run.thread_id,
            workflow_stage=ProductWorkStage.HUMAN_REVIEW,
            evidence_artifact_id="evidence-1",
            reviewed_mapping_artifact_id="mapping-1",
        )
    )

    decision = ProductReuseService(catalogue).decide(
        product.id,
        user_id="user-a",
        refresh_requested=False,
    )

    assert decision.mode is ReuseMode.CONTINUE_SAVED_WORK
    assert decision.evidence_artifact_id == "evidence-1"
    assert decision.reviewed_mapping_artifact_id == "mapping-1"


def test_refresh_never_silently_reuses_saved_work(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    catalogue.get_or_create_thread("thread-refresh", "user-a")
    product, _ = catalogue.get_or_create_product("https://example.com/refresh", user_id="user-a")

    decision = ProductReuseService(catalogue).decide(
        product.id,
        user_id="user-a",
        refresh_requested=True,
    )

    assert decision.mode is ReuseMode.REFRESH_SOURCES

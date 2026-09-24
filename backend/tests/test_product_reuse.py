from pathlib import Path

from mia_dpp.domain.product import BackgroundJobStatus, RunStatus
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



def test_newer_failed_snapshot_wins_over_older_deployable_dpp(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    catalogue.get_or_create_thread("thread-old-dpp", "user-a")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/old-dpp",
        user_id="user-a",
    )
    old_run = catalogue.start_run(product.id, "thread-old-dpp", user_id="user-a")
    catalogue.finish_run(old_run.id, RunStatus.COMPLETED)
    catalogue.create_dpp_version(
        product.id,
        old_run.id,
        dpp_artifact_id="dpp-old",
        deployable=True,
    )

    catalogue.get_or_create_thread("thread-new-work", "user-a")
    new_run = catalogue.start_run(product.id, "thread-new-work", user_id="user-a")
    catalogue.finish_run(new_run.id, RunStatus.FAILED, error="build failed")
    catalogue.save_product_work_snapshot(
        ProductWorkSnapshot(
            id="snapshot-new-work",
            user_id="user-a",
            product_id=product.id,
            run_id=new_run.id,
            thread_id=new_run.thread_id,
            workflow_stage=ProductWorkStage.FAILED,
            evidence_artifact_id="evidence-new",
            reviewed_mapping_artifact_id="mapping-new",
        )
    )

    decision = ProductReuseService(catalogue).decide(
        product.id,
        user_id="user-a",
        refresh_requested=False,
    )

    assert decision.mode is ReuseMode.CONTINUE_SAVED_WORK
    assert decision.seeded_from_run_id == new_run.id
    assert decision.evidence_artifact_id == "evidence-new"


def test_completed_research_after_dpp_prevents_stale_cache_reuse(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    catalogue.get_or_create_thread("thread-late-research", "user-a")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/late-research",
        user_id="user-a",
    )
    run = catalogue.start_run(product.id, "thread-late-research", user_id="user-a")
    catalogue.finish_run(run.id, RunStatus.COMPLETED)
    dpp = catalogue.create_dpp_version(
        product.id,
        run.id,
        dpp_artifact_id="dpp-current",
        deployable=True,
    )
    catalogue.save_product_work_snapshot(
        ProductWorkSnapshot(
            id="snapshot-late-research",
            user_id="user-a",
            product_id=product.id,
            run_id=run.id,
            thread_id=run.thread_id,
            workflow_stage=ProductWorkStage.COMPLETED,
            evidence_artifact_id="evidence-seed",
            dpp_artifact_id=dpp.dpp_artifact_id,
        )
    )
    job = catalogue.create_background_job(
        user_id="user-a",
        thread_id=run.thread_id,
        product_id=product.id,
        run_id=run.id,
        metadata={
            "seedEvidenceArtifactId": "evidence-seed",
            "researchEvidenceArtifactId": "evidence-research",
        },
    )
    catalogue.finish_background_job(
        job.id,
        user_id="user-a",
        status=BackgroundJobStatus.COMPLETED,
        metadata={"researchEvidenceArtifactId": "evidence-research"},
    )

    decision = ProductReuseService(catalogue).decide(
        product.id,
        user_id="user-a",
        refresh_requested=False,
    )

    assert decision.mode is ReuseMode.CONTINUE_SAVED_WORK
    assert decision.pending_research_job_id == job.id
    assert decision.evidence_artifact_id == "evidence-seed"



def test_missing_snapshot_artifact_falls_back_instead_of_crashing_reuse(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    catalogue.get_or_create_thread("thread-missing-artifact", "user-a")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/missing-artifact",
        user_id="user-a",
    )
    run = catalogue.start_run(
        product.id,
        "thread-missing-artifact",
        user_id="user-a",
    )
    catalogue.finish_run(run.id, RunStatus.FAILED, error="fixture")
    catalogue.save_product_work_snapshot(
        ProductWorkSnapshot(
            id="snapshot-missing-artifact",
            user_id="user-a",
            product_id=product.id,
            run_id=run.id,
            thread_id=run.thread_id,
            workflow_stage=ProductWorkStage.FAILED,
            evidence_artifact_id="artifact-that-does-not-exist",
        )
    )

    decision = ProductReuseService(catalogue).decide(
        product.id,
        user_id="user-a",
        refresh_requested=False,
    )

    assert decision.mode is ReuseMode.FRESH
    assert decision.evidence_artifact_id is None

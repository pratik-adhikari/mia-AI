from pathlib import Path

from mia_dpp.domain.product import BackgroundJobStatus, RunStatus
from mia_dpp.domain.product_work import ProductWorkSnapshot, ProductWorkStage, ReuseMode
from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.services.product_reuse import ProductReuseService
from mia_dpp.storage.models import StoredArtifact


def _artifact(
    catalogue: ProductCatalogue,
    run_id: str,
    artifact_id: str,
    *,
    key: str,
) -> StoredArtifact:
    run = catalogue.get_run(run_id)
    assert run is not None
    artifact = StoredArtifact(
        id=artifact_id,
        key=key,
        content_type="application/json",
        sha256="0" * 64,
        size=2,
        storage_uri=f"{run_id}/{key}",
        product_id=run.product_id,
        run_id=run_id,
    )
    catalogue.register_artifact(artifact)
    return artifact


def test_reuse_service_prefers_snapshot_before_legacy_artifact_scan(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    catalogue.get_or_create_thread("thread-reuse", "user-a")
    product, _ = catalogue.get_or_create_product("https://example.com/reuse", user_id="user-a")
    run = catalogue.start_run(product.id, "thread-reuse", user_id="user-a")
    _artifact(
        catalogue,
        run.id,
        "evidence-1",
        key="evidence/product-knowledge.json",
    )
    _artifact(
        catalogue,
        run.id,
        "mapping-1",
        key="mapping/reviewed.json",
    )
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
    _artifact(
        catalogue,
        old_run.id,
        "dpp-old",
        key="dpp/package.json",
    )
    catalogue.finish_run(old_run.id, RunStatus.COMPLETED)
    catalogue.create_dpp_version(
        product.id,
        old_run.id,
        dpp_artifact_id="dpp-old",
        deployable=True,
    )

    catalogue.get_or_create_thread("thread-new-work", "user-a")
    new_run = catalogue.start_run(product.id, "thread-new-work", user_id="user-a")
    _artifact(
        catalogue,
        new_run.id,
        "evidence-new",
        key="evidence/product-knowledge.json",
    )
    _artifact(
        catalogue,
        new_run.id,
        "mapping-new",
        key="mapping/reviewed.json",
    )
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
    _artifact(
        catalogue,
        run.id,
        "dpp-current",
        key="dpp/package.json",
    )
    _artifact(
        catalogue,
        run.id,
        "evidence-seed",
        key="evidence/product-knowledge.json",
    )
    _artifact(
        catalogue,
        run.id,
        "evidence-research",
        key="evidence/product-knowledge-research.json",
    )
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
            source_generation=1,
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
            "sourceGeneration": 1,
        },
    )
    catalogue.claim_background_job(job.id, user_id="user-a")
    catalogue.finish_background_job(
        job.id,
        user_id="user-a",
        status=BackgroundJobStatus.COMPLETED,
        metadata={
            "researchEvidenceArtifactId": "evidence-research",
            "sourceGeneration": 1,
        },
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


def test_missing_underlying_artifact_bytes_fall_back_to_fresh_work(tmp_path: Path) -> None:
    from mia_dpp.storage.local import LocalArtifactStore
    from mia_dpp.storage.models import StoredArtifact

    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    store = LocalArtifactStore(tmp_path / "artifacts")
    catalogue.get_or_create_thread("thread-missing-bytes", "user-a")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/missing-bytes",
        user_id="user-a",
    )
    run = catalogue.start_run(product.id, "thread-missing-bytes", user_id="user-a")
    catalogue.finish_run(run.id, RunStatus.FAILED, error="fixture")
    missing = StoredArtifact(
        id="artifact-missing-bytes",
        key="evidence/product-knowledge.json",
        content_type="application/json",
        sha256="0" * 64,
        size=2,
        storage_uri="run-does-not-exist/evidence/missing.json",
        product_id=product.id,
        run_id=run.id,
    )
    catalogue.register_artifact(missing)
    catalogue.save_product_work_snapshot(
        ProductWorkSnapshot(
            id="snapshot-missing-bytes",
            user_id="user-a",
            product_id=product.id,
            run_id=run.id,
            thread_id=run.thread_id,
            workflow_stage=ProductWorkStage.FAILED,
            evidence_artifact_id=missing.id,
        )
    )

    decision = ProductReuseService(catalogue, store).decide(
        product.id,
        user_id="user-a",
        refresh_requested=False,
    )

    assert decision.mode is ReuseMode.FRESH


def test_late_research_from_older_source_generation_is_ignored(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    catalogue.get_or_create_thread("thread-source-lineage", "user-a")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/source-lineage",
        user_id="user-a",
    )
    run_old = catalogue.start_run(product.id, "thread-source-lineage", user_id="user-a")
    _artifact(
        catalogue,
        run_old.id,
        "evidence-old",
        key="evidence/product-knowledge.json",
    )
    old_job = catalogue.create_background_job(
        user_id="user-a",
        thread_id=run_old.thread_id,
        product_id=product.id,
        run_id=run_old.id,
        metadata={
            "seedEvidenceArtifactId": "evidence-old",
            "sourceGeneration": 1,
        },
    )
    catalogue.finish_run(run_old.id, RunStatus.FAILED, error="refresh superseded")

    catalogue.advance_thread_workflow_generation(run_old.thread_id, user_id="user-a")
    run_new = catalogue.start_run(product.id, run_old.thread_id, user_id="user-a")
    _artifact(
        catalogue,
        run_new.id,
        "evidence-new-generation",
        key="evidence/product-knowledge.json",
    )
    catalogue.save_product_work_snapshot(
        ProductWorkSnapshot(
            id="snapshot-source-lineage",
            user_id="user-a",
            product_id=product.id,
            run_id=run_new.id,
            thread_id=run_new.thread_id,
            workflow_stage=ProductWorkStage.EVIDENCE,
            source_generation=2,
            evidence_artifact_id="evidence-new-generation",
        )
    )
    catalogue.finish_background_job(
        old_job.id,
        user_id="user-a",
        status=BackgroundJobStatus.COMPLETED,
        metadata={"sourceGeneration": 1},
    )

    decision = ProductReuseService(catalogue).decide(
        product.id,
        user_id="user-a",
        refresh_requested=False,
    )

    assert decision.mode is ReuseMode.CONTINUE_SAVED_WORK
    assert decision.pending_research_job_id is None
    assert decision.evidence_artifact_id == "evidence-new-generation"

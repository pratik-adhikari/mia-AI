from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.product import RunStatus
from mia_dpp.domain.product_work import ProductWorkStage, ReuseMode
from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.runtime.run_context import RunContext
from mia_dpp.runtime.run_store import RunStore
from mia_dpp.services.product_lifecycle import (
    ProductLifecycleService,
    ProductResolutionRequest,
)
from mia_dpp.services.product_snapshot import update_product_snapshot
from mia_dpp.storage.local import LocalArtifactStore


def test_product_lifecycle_and_snapshot_run_without_langgraph(tmp_path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    catalogue.get_or_create_thread("thread-a", "user-a")
    service = ProductLifecycleService(catalogue, artifacts)

    first = service.resolve(
        ProductResolutionRequest(
            product_url="https://example.com/products/widget?utm_source=test",
            user_id="user-a",
            thread_id="thread-a",
        )
    )

    assert first.reuse_mode is ReuseMode.FRESH
    assert first.status == "running"
    assert first.source_generation == 1

    context = RunContext(
        user_id="user-a",
        thread_id="thread-a",
        product_id=first.product_id,
        run_id=first.run_id,
    )
    snapshot = update_product_snapshot(
        catalogue,
        context,
        ProductWorkStage.EVIDENCE,
        expected_version=0,
        source_generation=first.source_generation,
        evidence_artifact_id="evidence-direct",
    )

    assert snapshot.product_id == first.product_id
    assert snapshot.run_id == first.run_id
    assert snapshot.workflow_stage is ProductWorkStage.EVIDENCE
    assert snapshot.evidence_artifact_id == "evidence-direct"

    resumed = service.resolve(
        ProductResolutionRequest(
            product_url="https://example.com/products/widget",
            user_id="user-a",
            thread_id="thread-a",
        )
    )

    assert resumed.run_id == first.run_id
    assert resumed.reuse_mode is ReuseMode.RESUME_CHECKPOINT
    assert resumed.evidence_artifact_id == "evidence-direct"
    assert resumed.product_snapshot_version == snapshot.version


def test_product_lifecycle_continues_real_persisted_saved_work(tmp_path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    catalogue.get_or_create_thread("thread-a", "user-a")
    service = ProductLifecycleService(catalogue, artifacts)

    first = service.resolve(
        ProductResolutionRequest(
            product_url="https://example.com/products/widget",
            user_id="user-a",
            thread_id="thread-a",
        )
    )
    context = RunContext(
        user_id="user-a",
        thread_id="thread-a",
        product_id=first.product_id,
        run_id=first.run_id,
    )
    store = RunStore(context, catalogue, artifacts)
    evidence_id = store.put_model(
        "evidence/product-knowledge.json",
        ProductKnowledgePackage(
            product_id=first.product_id,
            product_name="Widget",
            evidence=(),
        ),
    )
    snapshot = update_product_snapshot(
        catalogue,
        context,
        ProductWorkStage.EVIDENCE,
        expected_version=0,
        source_generation=first.source_generation,
        evidence_artifact_id=evidence_id,
    )
    catalogue.finish_run(first.run_id, RunStatus.INCOMPLETE)

    continued = service.resolve(
        ProductResolutionRequest(
            product_url="https://example.com/products/widget",
            user_id="user-a",
            thread_id="thread-a",
        )
    )

    assert continued.run_id != first.run_id
    assert continued.reuse_mode is ReuseMode.CONTINUE_SAVED_WORK
    assert continued.reuse_prior_work is True
    assert continued.seeded_from_run_id == first.run_id
    assert continued.evidence_artifact_id == evidence_id
    assert continued.product_snapshot_version == snapshot.version

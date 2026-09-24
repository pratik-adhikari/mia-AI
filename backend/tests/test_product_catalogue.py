import pytest
"""Durable product identity/history behavior independent of the agent runtime."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mia_dpp.domain.product import BackgroundJobStatus, MessageRole, RunStatus
from mia_dpp.domain.product_work import (
    HumanReviewAction,
    HumanReviewRecord,
    ProductWorkSnapshot,
    ProductWorkStage,
)
from mia_dpp.persistence.catalogue import ProductCatalogue, ProductSnapshotConflict
from mia_dpp.workflow.identity import canonical_product_url


def test_canonical_url_removes_tracking_and_normalizes_host() -> None:
    left = "HTTPS://WWW.Example.com/products/42/?utm_source=x&variant=red#details"
    right = "https://example.com/products/42?variant=red"
    assert canonical_product_url(left) == canonical_product_url(right)


def test_same_product_url_reuses_product_identity(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    first, created = catalogue.get_or_create_product(
        "https://www.example.com/product/42/?utm_source=mail"
    )
    second, created_again = catalogue.get_or_create_product("https://example.com/product/42")
    assert created is True
    assert created_again is False
    assert second.id == first.id


def test_concurrent_product_resolution_creates_one_identity(tmp_path: Path) -> None:
    path = tmp_path / "catalogue.sqlite3"

    def resolve(_: int):
        return ProductCatalogue(path).get_or_create_product(
            "https://example.com/product/42?utm_source=concurrent"
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        resolved = tuple(executor.map(resolve, range(8)))

    assert len({product.id for product, _ in resolved}) == 1
    assert sum(created for _, created in resolved) == 1


def test_attempts_messages_and_versions_survive_restart(tmp_path: Path) -> None:
    path = tmp_path / "catalogue.sqlite3"
    catalogue = ProductCatalogue(path)
    product, _ = catalogue.get_or_create_product("https://example.com/product/42")
    failed = catalogue.start_run(product.id, "thread-catalogue")
    catalogue.add_message("thread-catalogue", MessageRole.USER, "Create the DPP", run_id=failed.id)
    catalogue.finish_run(failed.id, RunStatus.FAILED, error="fixture failure")
    successful = catalogue.start_run(product.id, "thread-catalogue")
    version = catalogue.create_dpp_version(
        product.id,
        successful.id,
        dpp_artifact_id="artifact-dpp",
        deployable=True,
    )
    catalogue.finish_run(successful.id, RunStatus.COMPLETED)

    restarted = ProductCatalogue(path)
    assert [item.status for item in restarted.list_runs(product.id)] == [
        RunStatus.COMPLETED,
        RunStatus.FAILED,
    ]
    assert restarted.list_messages("thread-catalogue")[0].timestamp.tzinfo is not None
    assert restarted.latest_successful_dpp(product.id) == version


def test_concurrent_dpp_versions_are_unique_and_sequential(tmp_path: Path) -> None:
    path = tmp_path / "catalogue.sqlite3"
    catalogue = ProductCatalogue(path)
    product, _ = catalogue.get_or_create_product("https://example.com/product/versioned")
    runs = tuple(catalogue.start_run(product.id, f"thread-{index}") for index in range(6))

    def create(index: int):
        return ProductCatalogue(path).create_dpp_version(
            product.id,
            runs[index].id,
            dpp_artifact_id=f"artifact-{index}",
            deployable=True,
        )

    with ThreadPoolExecutor(max_workers=3) as executor:
        versions = tuple(executor.map(create, range(len(runs))))

    assert sorted(item.version for item in versions) == list(range(1, len(runs) + 1))


def test_account_ownership_hides_threads_products_and_messages(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    catalogue.get_or_create_thread("thread-user-a", "user-a")
    product, _ = catalogue.get_or_create_product("https://example.com/private", user_id="user-a")
    run = catalogue.start_run(product.id, "thread-user-a", user_id="user-a")
    catalogue.add_message(
        "thread-user-a",
        MessageRole.USER,
        "private message",
        run_id=run.id,
        user_id="user-a",
    )

    assert catalogue.get_thread("thread-user-a", user_id="user-b") is None
    assert catalogue.get_product(product.id, user_id="user-b") is None
    assert catalogue.list_messages("thread-user-a", user_id="user-b") == ()
    assert catalogue.list_products(user_id="user-b") == ()


def test_background_job_claim_and_retry_are_idempotent(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    catalogue.get_or_create_thread("thread-jobs", "user-a")
    product, _ = catalogue.get_or_create_product("https://example.com/job", user_id="user-a")
    run = catalogue.start_run(product.id, "thread-jobs", user_id="user-a")
    first = catalogue.create_background_job(
        user_id="user-a",
        thread_id="thread-jobs",
        product_id=product.id,
        run_id=run.id,
    )
    duplicate = catalogue.create_background_job(
        user_id="user-a",
        thread_id="thread-jobs",
        product_id=product.id,
        run_id=run.id,
    )

    assert duplicate.id == first.id
    assert catalogue.claim_background_job(first.id, user_id="user-a") is not None
    assert catalogue.claim_background_job(first.id, user_id="user-a") is None
    failed = catalogue.finish_background_job(
        first.id,
        user_id="user-a",
        status=BackgroundJobStatus.FAILED,
        error="retry fixture",
    )
    assert failed.status is BackgroundJobStatus.FAILED
    assert catalogue.claim_background_job(first.id, user_id="user-a") is not None


def test_deleted_chat_is_hidden_but_product_history_remains_reusable(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    catalogue.get_or_create_thread("thread-delete", "user-a")
    product, _ = catalogue.get_or_create_product("https://example.com/reusable", user_id="user-a")
    run = catalogue.start_run(product.id, "thread-delete", user_id="user-a")
    catalogue.add_message(
        "thread-delete",
        MessageRole.USER,
        "private chat",
        run_id=run.id,
        user_id="user-a",
    )

    deleted = catalogue.delete_thread("thread-delete", user_id="user-a")

    assert deleted.deleted_at is not None
    assert catalogue.list_threads("user-a") == ()
    assert catalogue.get_product(product.id, user_id="user-a") == product
    assert catalogue.list_runs(product.id, user_id="user-a")[0].id == run.id


def test_product_work_snapshot_is_user_scoped_and_versioned(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    catalogue.get_or_create_thread("thread-snapshot", "user-a")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/snapshot",
        user_id="user-a",
    )
    run = catalogue.start_run(product.id, "thread-snapshot", user_id="user-a")
    first = catalogue.save_product_work_snapshot(
        ProductWorkSnapshot(
            id="snapshot-one",
            user_id="user-a",
            product_id=product.id,
            run_id=run.id,
            thread_id=run.thread_id,
            workflow_stage=ProductWorkStage.EVIDENCE,
            evidence_artifact_id="artifact-evidence",
        )
    )
    second = catalogue.save_product_work_snapshot(
        first.model_copy(
            update={
                "workflow_stage": ProductWorkStage.MAPPING,
                "semantic_mapping_artifact_id": "artifact-mapping",
            }
        )
    )

    assert first.version == 1
    assert second.version == 2
    assert catalogue.get_product_work_snapshot(product.id, user_id="user-a") == second
    assert catalogue.get_product_work_snapshot(product.id, user_id="user-b") is None


def test_human_review_history_is_append_only_and_user_scoped(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    catalogue.get_or_create_thread("thread-review-audit", "user-a")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/review-audit",
        user_id="user-a",
    )
    run = catalogue.start_run(product.id, "thread-review-audit", user_id="user-a")
    review = HumanReviewRecord(
        id="human-review-1",
        user_id="user-a",
        product_id=product.id,
        run_id=run.id,
        thread_id=run.thread_id,
        action=HumanReviewAction.SUPPLIED_DUMMY,
        actor_name="Pratik",
        final_requirement_id="req-example",
        value_kind="dummy",
    )

    catalogue.add_human_review(review)

    assert catalogue.list_human_reviews(product.id, user_id="user-a") == (review,)
    assert catalogue.list_human_reviews(product.id, user_id="user-b") == ()


def test_mapping_knowledge_is_private_to_the_reviewing_user(tmp_path: Path) -> None:
    from mia_dpp.domain.mappings import (
        FieldMapping,
        MappingAssessment,
        MappingBasis,
        MappingOrigin,
        MappingStatus,
        MappingTarget,
    )
    from mia_dpp.domain.targets import ReferenceKey, SemanticReference

    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    target = MappingTarget(
        template_key="digital_nameplate",
        template_release="3.0.1",
        template_path=("Nameplate", "ManufacturerName"),
        instance_path=("Nameplate", "ManufacturerName"),
        id_short="ManufacturerName",
        semantic_id=SemanticReference(
            type="ExternalReference",
            keys=(ReferenceKey(type="GlobalReference", value="0173-1#02-AAO677#002"),),
        ),
    )
    mapping = FieldMapping(
        id="mapping-private",
        evidence_id="evidence-private",
        source_field="Manufacturer",
        source_value="Example AG",
        target=target,
        assessment=MappingAssessment(
            basis=MappingBasis.HUMAN,
            review_required=False,
            reason="Human confirmed.",
        ),
        reasoning="fixture",
        status=MappingStatus.APPROVED,
        mapping_origin=MappingOrigin.HUMAN,
        human_reviewed=True,
        human_value_kind="verified",
    )

    catalogue.remember_mapping_review(
        mapping,
        decision="keep",
        manufacturer="Example AG",
        domain="example.com",
        product_family=None,
        comment=None,
        user_id="user-a",
    )

    assert len(catalogue.list_mapping_knowledge(user_id="user-a")) == 1
    assert catalogue.list_mapping_knowledge(user_id="user-b") == ()



def test_stale_snapshot_write_cannot_overwrite_newer_product_state(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    catalogue.get_or_create_thread("thread-lock", "user-a")
    product, _ = catalogue.get_or_create_product("https://example.com/lock", user_id="user-a")
    run = catalogue.start_run(product.id, "thread-lock", user_id="user-a")
    first = catalogue.save_product_work_snapshot(
        ProductWorkSnapshot(
            id="snapshot-lock",
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

    with pytest.raises(ProductSnapshotConflict):
        catalogue.save_product_work_snapshot(
            first.model_copy(update={"workflow_stage": ProductWorkStage.COVERAGE}),
            expected_version=first.version,
        )

    assert catalogue.get_product_work_snapshot(product.id, user_id="user-a") == newer

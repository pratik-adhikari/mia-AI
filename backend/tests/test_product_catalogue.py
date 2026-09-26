"""Durable product identity/history behavior independent of the agent runtime."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import pytest

from mia_dpp.domain.product import (
    BackgroundJobStatus,
    DppReleaseStatus,
    MessageRole,
    RunStatus,
)
from mia_dpp.domain.product_identity import canonical_product_url
from mia_dpp.domain.product_work import (
    HumanReviewAction,
    HumanReviewRecord,
    ProductWorkSnapshot,
    ProductWorkStage,
)
from mia_dpp.persistence.catalogue import ProductCatalogue, ProductSnapshotConflict
from mia_dpp.storage.models import StoredArtifact


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
    created_runs = []
    for index in range(6):
        run = catalogue.start_run(product.id, f"thread-{index}")
        catalogue.finish_run(run.id, RunStatus.COMPLETED)
        created_runs.append(run)
    runs = tuple(created_runs)

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


def test_latest_completed_background_job_uses_stored_completion_time(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    catalogue.get_or_create_thread("thread-completed-jobs", "user-a")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/completed-jobs", user_id="user-a"
    )
    run = catalogue.start_run(product.id, "thread-completed-jobs", user_id="user-a")
    assert catalogue.latest_completed_background_job(product.id, user_id="user-a") is None

    first = catalogue.create_background_job(
        user_id="user-a",
        thread_id=run.thread_id,
        product_id=product.id,
        run_id=run.id,
        job_type="first",
        metadata={"sourceGeneration": 1},
    )
    second = catalogue.create_background_job(
        user_id="user-a",
        thread_id=run.thread_id,
        product_id=product.id,
        run_id=run.id,
        job_type="second",
        metadata={"sourceGeneration": 2},
    )
    for job in (first, second):
        catalogue.claim_background_job(job.id, user_id="user-a")
        catalogue.finish_background_job(
            job.id,
            user_id="user-a",
            status=BackgroundJobStatus.COMPLETED,
        )

    assert catalogue.latest_completed_background_job(product.id, user_id="user-a").id == second.id
    assert (
        catalogue.latest_completed_background_job(
            product.id, user_id="user-a", source_generation=1
        ).id
        == first.id
    )


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
    history = catalogue.list_product_work_snapshot_history(product.id, user_id="user-a")
    assert [item.version for item in history] == [1, 2]
    assert history[-1] == newer


def test_dpp_with_dummy_values_is_saved_as_provisional(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    catalogue.get_or_create_thread("thread-provisional", "user-a")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/provisional",
        user_id="user-a",
    )
    run = catalogue.start_run(product.id, "thread-provisional", user_id="user-a")

    version = catalogue.create_dpp_version(
        product.id,
        run.id,
        dpp_artifact_id="dpp-artifact",
        deployable=True,
        release_status=DppReleaseStatus.PROVISIONAL,
        dummy_mapping_ids=("mapping-dummy",),
    )

    assert version.deployable is True
    assert version.release_status is DppReleaseStatus.PROVISIONAL
    assert version.dummy_mapping_ids == ("mapping-dummy",)
    assert catalogue.latest_successful_dpp(product.id, user_id="user-a") == version


def test_same_product_cannot_have_two_active_runs_for_one_user(tmp_path: Path) -> None:
    from mia_dpp.persistence.catalogue import ActiveProductRunExists

    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    catalogue.get_or_create_thread("thread-active-one", "user-a")
    catalogue.get_or_create_thread("thread-active-two", "user-a")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/single-active",
        user_id="user-a",
    )
    first = catalogue.start_run(
        product.id,
        "thread-active-one",
        user_id="user-a",
    )

    with pytest.raises(ActiveProductRunExists) as error:
        catalogue.start_run(
            product.id,
            "thread-active-two",
            user_id="user-a",
        )

    assert error.value.run.id == first.id
    assert error.value.run.thread_id == "thread-active-one"


def test_deleted_active_chat_no_longer_blocks_new_product_work(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    catalogue.get_or_create_thread("thread-delete-active", "user-a")
    catalogue.get_or_create_thread("thread-after-delete", "user-a")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/delete-active",
        user_id="user-a",
    )
    first = catalogue.start_run(
        product.id,
        "thread-delete-active",
        user_id="user-a",
    )

    catalogue.delete_thread("thread-delete-active", user_id="user-a")
    replacement = catalogue.start_run(
        product.id,
        "thread-after-delete",
        user_id="user-a",
    )

    assert catalogue.get_run(first.id).status is RunStatus.INCOMPLETE
    assert replacement.thread_id == "thread-after-delete"


def test_artifact_access_is_scoped_to_the_producing_thread_owner(tmp_path: Path) -> None:
    from mia_dpp.storage.models import StoredArtifact

    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/shared-artifact-product",
        user_id="user-a",
    )
    catalogue.get_or_create_product(
        "https://example.com/shared-artifact-product",
        user_id="user-b",
    )
    catalogue.get_or_create_thread("thread-owner-a", "user-a")
    run = catalogue.start_run(product.id, "thread-owner-a", user_id="user-a")
    artifact = StoredArtifact(
        id="artifact-private-a",
        key="evidence/private.json",
        content_type="application/json",
        sha256="0" * 64,
        size=2,
        storage_uri="memory://artifact-private-a",
        product_id=product.id,
        run_id=run.id,
    )
    catalogue.register_artifact(artifact)

    assert catalogue.get_artifact(artifact.id, user_id="user-a") == artifact
    assert catalogue.get_artifact(artifact.id, user_id="user-b") is None


@pytest.mark.parametrize("mutation", ("snapshot", "finish", "dpp", "artifact"))
def test_recovery_wins_lock_before_stale_authoritative_mutation(
    tmp_path: Path,
    mutation: str,
) -> None:
    catalogue = ProductCatalogue(tmp_path / f"race-{mutation}.sqlite3")
    catalogue.get_or_create_thread(f"thread-race-{mutation}", "user-a")
    product, _ = catalogue.get_or_create_product(
        f"https://example.com/race-{mutation}",
        user_id="user-a",
    )
    old = catalogue.start_run(product.id, f"thread-race-{mutation}", user_id="user-a")
    initial_snapshot = None
    if mutation == "snapshot":
        initial_snapshot = catalogue.save_product_work_snapshot(
            ProductWorkSnapshot(
                id=f"snapshot-race-{mutation}",
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

    recovery_has_lock = Event()
    allow_recovery = Event()
    original_lease_check = catalogue.run_lease_is_live

    def paused_lease_check(run, *, now=None):
        recovery_has_lock.set()
        assert allow_recovery.wait(timeout=5)
        return original_lease_check(run, now=now)

    catalogue.run_lease_is_live = paused_lease_check  # type: ignore[method-assign]

    def recover():
        return catalogue.claim_product_restart(
            user_id="user-a",
            product_id=product.id,
            expected_run_id=old.id,
            expected_generation=0,
            reason="race recovery",
            refresh_requested=False,
            require_expired_lease=True,
        )

    def stale_mutation():
        if mutation == "snapshot":
            assert initial_snapshot is not None
            return catalogue.save_product_work_snapshot(
                initial_snapshot.model_copy(update={"workflow_stage": ProductWorkStage.MAPPING}),
                expected_version=initial_snapshot.version,
            )
        if mutation == "finish":
            return catalogue.finish_run(old.id, RunStatus.COMPLETED)
        if mutation == "dpp":
            return catalogue.create_dpp_version(
                product.id,
                old.id,
                dpp_artifact_id="dpp-race",
                deployable=True,
            )
        return catalogue.register_artifact(
            StoredArtifact(
                id="artifact-race",
                key="evidence/race.json",
                content_type="application/json",
                sha256="0" * 64,
                size=2,
                storage_uri="race/evidence.json",
                product_id=product.id,
                run_id=old.id,
            )
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        recovery_future = pool.submit(recover)
        assert recovery_has_lock.wait(timeout=5)
        stale_future = pool.submit(stale_mutation)
        allow_recovery.set()
        replacement = recovery_future.result(timeout=5)
        with pytest.raises(RuntimeError, match="workflow generation"):
            stale_future.result(timeout=5)

    assert catalogue.get_run(replacement.id).status is RunStatus.RUNNING
    if mutation == "snapshot":
        assert initial_snapshot is not None
        assert (
            catalogue.get_product_work_snapshot(
                product.id,
                user_id="user-a",
            )
            == initial_snapshot
        )
    elif mutation == "dpp":
        assert catalogue.list_dpp_versions(product.id, user_id="user-a") == ()
    elif mutation == "artifact":
        assert catalogue.get_artifact("artifact-race", user_id="user-a") is None


def test_recovery_supersedes_and_requeues_background_research(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "research-recovery.sqlite3")
    catalogue.get_or_create_thread("thread-research-recovery", "user-a")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/research-recovery",
        user_id="user-a",
    )
    old = catalogue.start_run(product.id, "thread-research-recovery", user_id="user-a")
    job = catalogue.create_background_job(
        user_id="user-a",
        thread_id=old.thread_id,
        product_id=product.id,
        run_id=old.id,
        metadata={
            "seedEvidenceArtifactId": "evidence-seed",
            "sourceGeneration": 3,
            "phase": "queued",
        },
    )
    claimed = catalogue.claim_background_job(job.id, user_id="user-a")
    assert claimed is not None and claimed.status is BackgroundJobStatus.RUNNING

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
        reason="recover foreground",
        refresh_requested=False,
        require_expired_lease=True,
    )

    old_job = catalogue.get_background_job(job.id, user_id="user-a")
    jobs = catalogue.list_background_jobs(user_id="user-a", thread_id=old.thread_id)
    successor = next(item for item in jobs if item.run_id == replacement.id)

    assert old_job is not None and old_job.status is BackgroundJobStatus.CANCELLED
    assert successor.status is BackgroundJobStatus.QUEUED
    assert successor.metadata["sourceGeneration"] == 3
    assert successor.metadata["supersededJobId"] == job.id


def test_stale_snapshot_write_commits_before_recovery_when_it_holds_lock_first(
    tmp_path: Path,
) -> None:
    catalogue = ProductCatalogue(tmp_path / "race-old-first.sqlite3")
    catalogue.get_or_create_thread("thread-race-old-first", "user-a")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/race-old-first",
        user_id="user-a",
    )
    old = catalogue.start_run(product.id, "thread-race-old-first", user_id="user-a")
    first = catalogue.save_product_work_snapshot(
        ProductWorkSnapshot(
            id="snapshot-race-old-first",
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

    writer_has_lock = Event()
    allow_writer = Event()
    original_lock = catalogue._lock_current_run

    def paused_lock(db, run_id):
        run, thread = original_lock(db, run_id)
        writer_has_lock.set()
        assert allow_writer.wait(timeout=5)
        return run, thread

    catalogue._lock_current_run = paused_lock  # type: ignore[method-assign]

    def write_snapshot():
        return catalogue.save_product_work_snapshot(
            first.model_copy(update={"workflow_stage": ProductWorkStage.MAPPING}),
            expected_version=first.version,
        )

    def recover():
        return catalogue.claim_product_restart(
            user_id="user-a",
            product_id=product.id,
            expected_run_id=old.id,
            expected_generation=0,
            reason="race recovery",
            refresh_requested=False,
            require_expired_lease=True,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        write_future = pool.submit(write_snapshot)
        assert writer_has_lock.wait(timeout=5)
        recovery_future = pool.submit(recover)
        allow_writer.set()
        written = write_future.result(timeout=5)
        replacement = recovery_future.result(timeout=5)

    assert written.version == 2
    assert catalogue.get_product_work_snapshot(product.id, user_id="user-a") == written
    assert catalogue.get_run(replacement.id).status is RunStatus.RUNNING


def test_completed_research_is_not_requeued_during_recovery(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "completed-research-recovery.sqlite3")
    catalogue.get_or_create_thread("thread-completed-research", "user-a")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/completed-research",
        user_id="user-a",
    )
    old = catalogue.start_run(
        product.id,
        "thread-completed-research",
        user_id="user-a",
    )
    job = catalogue.create_background_job(
        user_id="user-a",
        thread_id=old.thread_id,
        product_id=product.id,
        run_id=old.id,
        metadata={"sourceGeneration": 5},
    )
    catalogue.claim_background_job(job.id, user_id="user-a")
    completed = catalogue.finish_background_job(
        job.id,
        user_id="user-a",
        status=BackgroundJobStatus.COMPLETED,
        metadata={"sourceGeneration": 5},
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
        reason="recover after research completion",
        refresh_requested=False,
        require_expired_lease=True,
    )

    jobs = catalogue.list_background_jobs(
        user_id="user-a",
        thread_id=old.thread_id,
    )
    assert completed in jobs
    assert not any(item.run_id == replacement.id for item in jobs)

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from mia_dpp.domain.product import BackgroundJobStatus
from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.services.deep_research import DeepResearchService


def test_recovery_after_research_claim_never_leaves_job_running(tmp_path) -> None:
    catalogue = ProductCatalogue(tmp_path / "research-claim-race.sqlite3")
    catalogue.get_or_create_thread("thread-research-claim", "user-a")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/research-claim",
        user_id="user-a",
    )
    old = catalogue.start_run(
        product.id,
        "thread-research-claim",
        user_id="user-a",
    )
    job = catalogue.create_background_job(
        user_id="user-a",
        thread_id=old.thread_id,
        product_id=product.id,
        run_id=old.id,
        metadata={
            "seedEvidenceArtifactId": "evidence-seed",
            "seedUrl": product.canonical_url,
            "sourceGeneration": 4,
        },
    )
    expired = old.model_copy(
        update={"execution_lease_expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    catalogue._execute(
        "UPDATE runs SET payload=? WHERE id=?",
        (expired.model_dump_json(), old.id),
    )

    original_claim = catalogue.claim_background_job
    holder: dict[str, str] = {}

    def claim_then_recover(job_id: str, *, user_id: str):
        claimed = original_claim(job_id, user_id=user_id)
        assert claimed is not None
        replacement = catalogue.claim_product_restart(
            user_id=user_id,
            product_id=product.id,
            expected_run_id=old.id,
            expected_generation=0,
            reason="foreground recovered after research claim",
            refresh_requested=False,
            require_expired_lease=True,
        )
        holder["replacement"] = replacement.id
        return claimed

    catalogue.claim_background_job = claim_then_recover  # type: ignore[method-assign]
    service = DeepResearchService(SimpleNamespace(catalogue=catalogue))

    with pytest.raises(RuntimeError, match="workflow generation"):
        asyncio.run(service.run(job.id, user_id="user-a"))

    old_job = catalogue.get_background_job(job.id, user_id="user-a")
    jobs = catalogue.list_background_jobs(
        user_id="user-a",
        thread_id=old.thread_id,
    )
    successor = next(item for item in jobs if item.run_id == holder["replacement"])

    assert old_job is not None
    assert old_job.status is BackgroundJobStatus.CANCELLED
    assert successor.status is BackgroundJobStatus.QUEUED
    assert successor.metadata["sourceGeneration"] == 4
    assert all(item.status is not BackgroundJobStatus.RUNNING for item in jobs)

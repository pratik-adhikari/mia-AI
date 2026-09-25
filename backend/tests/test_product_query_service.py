"""Durable read-model tests for the conversation plane."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from mia_dpp.domain.evidence import (
    EvidenceRecord,
    EvidenceStatus,
    ProductKnowledgePackage,
    SourceLocation,
)
from mia_dpp.domain.product_work import ProductWorkSnapshot, ProductWorkStage
from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.services.product_query import ProductQueryService
from mia_dpp.storage.local import LocalArtifactStore


def test_product_query_reads_status_and_evidence_without_checkpoint_state(
    tmp_path: Path,
) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    thread_id = "thread-query-service"
    product, _ = catalogue.get_or_create_product(
        "https://manufacturer.example/robot",
    )
    product = catalogue.update_product(product.model_copy(update={"name": "Industrial Robot X"}))
    run = catalogue.start_run(product.id, thread_id)

    record = EvidenceRecord(
        id="ev-voltage",
        predicate="technical.voltage",
        source_label="Supply voltage",
        value="48",
        unit="V",
        context_path=("Technical Specifications", "Electrical"),
        source_uri="https://manufacturer.example/robot",
        source_content_sha256=hashlib.sha256(b"fixture").hexdigest(),
        source_location=SourceLocation(
            excerpt="Supply voltage: 48 V",
        ),
        extraction_method="fixture",
        extractor_name="fixture",
        extractor_version="1",
        status=EvidenceStatus.OBSERVED,
        acquired_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    package = ProductKnowledgePackage(
        product_id=product.id,
        product_name="Industrial Robot X",
        evidence=(record,),
    )
    artifact = artifacts.put(
        "evidence/product-knowledge.json",
        package.model_dump_json(by_alias=True).encode(),
        content_type="application/json",
        product_id=product.id,
        run_id=run.id,
    )
    catalogue.register_artifact(artifact)
    catalogue.save_product_work_snapshot(
        ProductWorkSnapshot(
            id=f"snapshot-{product.id}",
            user_id="local-development",
            product_id=product.id,
            run_id=run.id,
            thread_id=thread_id,
            workflow_stage=ProductWorkStage.EVIDENCE,
            evidence_artifact_id=artifact.id,
        )
    )
    catalogue.add_event(
        run.id,
        "evidence.extraction_completed",
        "Extracted source-backed product evidence.",
        metadata={"evidenceCount": 1},
    )

    query = ProductQueryService(catalogue, artifacts)
    status = query.work_status(thread_id, user_id="local-development")
    hits = query.search_evidence(
        thread_id,
        "voltage",
        user_id="local-development",
    )

    assert status.product_id == product.id
    assert status.product_name == "Industrial Robot X"
    assert status.run_id == run.id
    assert status.workflow_stage is ProductWorkStage.EVIDENCE
    assert status.lease_live is True
    assert status.recent_events[-1].event_type == "evidence.extraction_completed"

    assert len(hits) == 1
    assert hits[0].evidence_id == record.id
    assert hits[0].value == "48"
    assert hits[0].unit == "V"
    assert hits[0].context_path == ("Technical Specifications", "Electrical")
    assert hits[0].source_uri == record.source_uri

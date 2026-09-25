from datetime import UTC, datetime

from mia_dpp.aas.requirements import build_template_index
from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.evidence import (
    EvidenceRecord,
    EvidenceStatus,
    ProductKnowledgePackage,
    SourceLocation,
    SourceType,
)
from mia_dpp.domain.mappings import (
    EvidenceOutcome,
    EvidenceOutcomeStatus,
    MappingOrigin,
    MappingResult,
)
from mia_dpp.tools.mapping.review import MappingReviewService


def _inventory():
    repository = OfficialTemplateRepository()
    return repository, build_template_index((repository.load("digital_nameplate"),))


def test_dummy_value_keeps_explicit_human_provenance() -> None:
    repository, index = _inventory()
    requirement = next(
        item
        for item in index.requirements
        if item.required and item.semantic_id is not None and not item.wildcard
    )
    package = ProductKnowledgePackage(
        product_id="product-dummy",
        product_name="Dummy fixture",
        evidence=(),
    )
    service = MappingReviewService(repository)

    package, result = service.record_human_value(
        package,
        MappingResult(),
        index,
        requirement_id=requirement.id,
        value="",
        thread_id="thread-dummy",
        actor_name="Pratik",
        use_dummy=True,
    )

    evidence = package.evidence[-1]
    mapping = result.mapped[-1]
    assert evidence.human_actor_name == "Pratik"
    assert evidence.human_value_kind == "dummy"
    assert evidence.predicate == "human.dummy"
    assert mapping.human_actor_name == "Pratik"
    assert mapping.human_value_kind == "dummy"


def test_human_audit_records_before_and_after_values_and_targets() -> None:
    from mia_dpp.agent.models import AgentReviewDecision
    from mia_dpp.domain.mappings import SemanticReviewItem
    from mia_dpp.services.human_review_audit import mapping_review_records

    repository, index = _inventory()
    requirement = next(
        item
        for item in index.requirements
        if item.semantic_id is not None and not item.wildcard and item.template_path
    )
    source = EvidenceRecord(
        id="evidence-audit-old",
        predicate="fixture",
        source_label="Fixture",
        value="IP65",
        source_type=SourceType.WEBSITE,
        source_uri="https://example.com/product",
        source_content_sha256="0" * 64,
        source_location=SourceLocation(excerpt="IP65"),
        extraction_method="fixture",
        extractor_name="fixture",
        extractor_version="1",
        status=EvidenceStatus.OBSERVED,
        acquired_at=datetime.now(UTC),
    )
    service = MappingReviewService(repository)
    package = ProductKnowledgePackage(
        product_id="product-audit",
        product_name="Fixture",
        evidence=(source,),
    )
    base = service.record_human_value(
        package,
        MappingResult(
            unmatched_evidence_ids=(source.id,),
            outcomes=(
                EvidenceOutcome(
                    evidence_id=source.id,
                    status=EvidenceOutcomeStatus.UNMAPPED,
                    reason="Fixture evidence awaits a mapping decision.",
                    mapping_origin=MappingOrigin.DETERMINISTIC,
                ),
            ),
        ),
        index,
        requirement_id=requirement.id,
        value="IP65",
        thread_id="thread-audit",
        actor_name="Pratik",
        use_dummy=False,
    )[1].mapped[-1]
    before = SemanticReviewItem(
        id="review-" + "a" * 24,
        evidence_id=base.evidence_id,
        status=EvidenceOutcomeStatus.MAPPED,
        requirement_id=requirement.id,
        reason="fixture",
        mapping=base,
    )
    after_mapping = base.model_copy(
        update={
            "id": "mapping-after",
            "source_value": "IP67",
        }
    )
    after = before.model_copy(update={"mapping": after_mapping})

    records = mapping_review_records(
        user_id="user-a",
        product_id="product-audit",
        run_id="run-audit",
        thread_id="thread-audit",
        mapping_cycle_id="cycle-audit",
        actor_name="Pratik",
        decision=AgentReviewDecision(
            review_id=before.id,
            decision="keep",
        ),
        before=before,
        after=after,
        proposed_value="IP65",
        final_value="IP67",
    )

    assert records[0].proposed_value == "IP65"
    assert records[0].final_value == "IP67"
    assert records[0].proposed_target_path == requirement.template_path
    assert records[0].final_target_path == requirement.template_path


def test_stale_generation_cannot_persist_human_audit_or_trusted_mapping(tmp_path) -> None:
    from datetime import timedelta
    from pathlib import Path

    import pytest

    from mia_dpp.domain.product import RunStatus
    from mia_dpp.domain.product_work import HumanReviewAction, HumanReviewRecord
    from mia_dpp.persistence.catalogue import ProductCatalogue

    catalogue = ProductCatalogue(Path(tmp_path) / "human-fence.sqlite3")
    catalogue.get_or_create_thread("thread-human-fence", "user-a")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/human-fence",
        user_id="user-a",
    )
    run = catalogue.start_run(product.id, "thread-human-fence", user_id="user-a")

    repository, index = _inventory()
    requirement = next(
        item for item in index.requirements if item.semantic_id is not None and not item.wildcard
    )
    package = ProductKnowledgePackage(
        product_id=product.id,
        product_name="Human fence fixture",
        evidence=(),
    )
    mapping = (
        MappingReviewService(repository)
        .record_human_value(
            package,
            MappingResult(),
            index,
            requirement_id=requirement.id,
            value="IP67",
            thread_id=run.thread_id,
            actor_name="Pratik",
            use_dummy=False,
        )[1]
        .mapped[-1]
    )

    expired = run.model_copy(
        update={"execution_lease_expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    catalogue._execute(
        "UPDATE runs SET payload=? WHERE id=?",
        (expired.model_dump_json(), run.id),
    )
    replacement = catalogue.claim_product_restart(
        user_id="user-a",
        product_id=product.id,
        expected_run_id=run.id,
        expected_generation=0,
        reason="refresh won",
        refresh_requested=False,
        require_expired_lease=True,
    )

    audit = HumanReviewRecord(
        id="review-stale-generation",
        user_id="user-a",
        product_id=product.id,
        run_id=run.id,
        thread_id=run.thread_id,
        action=HumanReviewAction.ACCEPTED_MAPPING,
        actor_name="Pratik",
        final_mapping_id=mapping.id,
        final_requirement_id=requirement.id,
    )

    with pytest.raises(RuntimeError, match="workflow generation"):
        catalogue.add_human_review(audit)
    with pytest.raises(RuntimeError, match="workflow generation"):
        catalogue.remember_mapping_review(
            mapping,
            decision="keep",
            manufacturer=None,
            domain="example.com",
            product_family=None,
            comment=None,
            actor_name="Pratik",
            user_id="user-a",
            run_id=run.id,
        )

    assert catalogue.list_human_reviews(product.id, user_id="user-a") == ()
    assert catalogue.list_mapping_knowledge(user_id="user-a") == ()
    assert catalogue.get_run(replacement.id).status is RunStatus.RUNNING

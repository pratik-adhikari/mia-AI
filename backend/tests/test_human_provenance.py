from datetime import UTC, datetime

from mia_dpp.aas.requirements import build_template_index
from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.evidence import (
    EvidenceRecord,
    ProductKnowledgePackage,
    SourceLocation,
    SourceType,
)
from mia_dpp.domain.mappings import EvidenceOutcomeStatus, MappingResult
from mia_dpp.tools.mapping.review import MappingReviewService


def _inventory():
    repository = OfficialTemplateRepository()
    return repository, build_template_index((repository.load("digital_nameplate"),))


def test_dummy_value_keeps_explicit_human_provenance() -> None:
    repository, index = _inventory()
    requirement = next(
        item for item in index.requirements
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
        MappingResult(),
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

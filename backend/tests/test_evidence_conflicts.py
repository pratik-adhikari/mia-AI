from datetime import UTC, datetime

from mia_dpp.domain.evidence import (
    EvidenceRecord,
    EvidenceStatus,
    ProductKnowledgePackage,
    SourceLocation,
    SourceType,
)
from mia_dpp.domain.mappings import (
    FieldMapping,
    MappingAssessment,
    MappingBasis,
    MappingOrigin,
    MappingResult,
    MappingStatus,
)
from mia_dpp.aas.requirements import build_template_index
from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.services.evidence_conflicts import (
    detect_review_conflicts,
    mark_conflicting_evidence,
    require_review_for_conflicts,
)
from mia_dpp.tools.mapping.targets import mapping_target


def _evidence(identifier: str, value: str) -> EvidenceRecord:
    return EvidenceRecord(
        id=identifier,
        predicate="protection.class",
        source_label="Protection class",
        value=value,
        source_type=SourceType.WEBSITE,
        source_uri=f"https://example.com/{identifier}",
        source_content_sha256="0" * 64,
        source_location=SourceLocation(excerpt=value),
        extraction_method="fixture",
        extractor_name="fixture",
        extractor_version="1",
        status=EvidenceStatus.OBSERVED,
        acquired_at=datetime.now(UTC),
    )


def test_new_value_reopens_only_the_reviewed_requirement() -> None:
    repository = OfficialTemplateRepository()
    template = repository.load("digital_nameplate")
    index = build_template_index((template,))
    requirement = next(
        item
        for item in index.requirements
        if item.semantic_id is not None and not item.wildcard and item.template_path
    )
    target = mapping_target(template, requirement.template_path)
    old = _evidence("ev-old", "IP65")
    new = _evidence("ev-new", "IP67")
    previous = FieldMapping(
        id="mapping-old",
        evidence_id=old.id,
        source_field="Protection class",
        source_value="IP65",
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
        human_actor_name="Pratik",
        human_value_kind="verified",
    )
    incoming = previous.model_copy(
        update={
            "id": "mapping-new",
            "evidence_id": new.id,
            "source_value": "IP67",
            "status": MappingStatus.AUTO,
            "human_reviewed": False,
            "human_actor_name": None,
            "mapping_origin": MappingOrigin.DETERMINISTIC,
        }
    )
    existing_package = ProductKnowledgePackage(
        product_id="product-1",
        product_name="Fixture",
        evidence=(old,),
    )
    incoming_package = ProductKnowledgePackage(
        product_id="product-1",
        product_name="Fixture",
        evidence=(new,),
    )

    conflicts = detect_review_conflicts(
        existing_package,
        incoming_package,
        MappingResult(mapped=(previous,)),
        MappingResult(mapped=(incoming,)),
        index,
    )

    assert len(conflicts) == 1
    assert conflicts[0].existing_value == "IP65"
    assert conflicts[0].incoming_value == "IP67"
    merged = MappingResult(mapped=(previous, incoming))
    reopened = require_review_for_conflicts(merged, index, conflicts)
    assert all(item.status is MappingStatus.REVIEW for item in reopened.mapped)
    marked = mark_conflicting_evidence(
        ProductKnowledgePackage(
            product_id="product-1",
            product_name="Fixture",
            evidence=(old, new),
        ),
        conflicts,
    )
    assert all(item.status is EvidenceStatus.CONFLICTING for item in marked.evidence)

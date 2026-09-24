"""Immutable audit coverage for direct verified semantic-target reviews."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.agent.models import AgentReviewDecision
from mia_dpp.domain.evidence import (
    EvidenceRecord,
    EvidenceStatus,
    SourceLocation,
)
from mia_dpp.domain.mappings import (
    EvidenceOutcomeStatus,
    MappingAssessment,
    MappingBasis,
    MappingOrigin,
    MappingStatus,
    SemanticReviewItem,
    FieldMapping,
)
from mia_dpp.domain.product_work import HumanReviewAction
from mia_dpp.semantic.open_property import technical_property_area_binding
from mia_dpp.services.human_review_audit import mapping_review_records
from mia_dpp.tools.mapping.targets import (
    TECHNICAL_DATA_ARBITRARY_PROPERTY_PATH,
    mapping_target,
)


def test_direct_semantic_selection_is_audited_as_target_correction() -> None:
    repository = OfficialTemplateRepository()
    template = repository.load("technical_data")
    context = ("Technical Specifications", "Motor A")
    binding = technical_property_area_binding(context)
    target = mapping_target(
        template,
        TECHNICAL_DATA_ARBITRARY_PROPERTY_PATH,
        id_short="RatedPower",
        semantic_id="0173-1#02-POWER#001",
        list_instance_bindings=(binding,),
    )
    evidence = EvidenceRecord(
        id="ev-1",
        predicate="source.rated-power",
        source_label="Rated power",
        value="500",
        unit="W",
        context_path=context,
        source_uri="urn:test:audit",
        source_content_sha256=hashlib.sha256(b"fixture").hexdigest(),
        source_location=SourceLocation(excerpt="Rated power: 500 W"),
        extraction_method="test",
        extractor_name="test",
        extractor_version="1",
        status=EvidenceStatus.OBSERVED,
        acquired_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    before = SemanticReviewItem(
        id="review-" + "a" * 24,
        evidence_id=evidence.id,
        status=EvidenceOutcomeStatus.UNCERTAIN,
        target_kind="direct",
        alternative_targets=(target,),
        review_priority="alarm",
        reason="Verified concepts require human selection.",
    )
    mapping = FieldMapping(
        id="mapping-" + "b" * 24,
        evidence_id=evidence.id,
        source_field="Rated power",
        source_value="500 W",
        target=target,
        assessment=MappingAssessment(
            basis=MappingBasis.HUMAN,
            review_required=False,
            reason="Human selected a verified ECLASS concept.",
        ),
        reasoning="Human confirmed direct semantic target.",
        status=MappingStatus.APPROVED,
        mapping_origin=MappingOrigin.HUMAN,
        human_reviewed=True,
        human_actor_name="Reviewer",
        human_value_kind="verified",
    )
    after = before.model_copy(
        update={
            "status": EvidenceOutcomeStatus.MAPPED,
            "reason": "Human selected and confirmed a verified semantic target.",
            "mapping": mapping,
        }
    )
    decision = AgentReviewDecision(
        review_id=before.id,
        decision="change_target",
        corrected_semantic_id=target.semantic_id.primary_value,
    )

    records = mapping_review_records(
        user_id="user-1",
        product_id="product-1",
        run_id="run-1",
        thread_id="thread-1",
        mapping_cycle_id="cycle-1",
        actor_name="Reviewer",
        decision=decision,
        before=before,
        after=after,
        proposed_value="500 W",
        final_value="500 W",
    )

    assert len(records) == 1
    record = records[0]
    assert record.action is HumanReviewAction.CORRECTED_TARGET
    assert record.proposed_semantic_id is None
    assert record.final_semantic_id == "0173-1#02-POWER#001"
    assert record.final_list_instance_bindings == (binding,)
    assert record.final_target_path == TECHNICAL_DATA_ARBITRARY_PROPERTY_PATH

"""Shadow Technical Data open-property proposal tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.evidence import (
    EvidenceRecord,
    EvidenceStatus,
    ProductKnowledgePackage,
    SourceLocation,
)
from mia_dpp.normalization import normalize_package
from mia_dpp.semantic.decision_policy import DecisionPolicySettings, DecisionPriority
from mia_dpp.semantic.eclass import EclassProperty
from mia_dpp.semantic.eclass_diagnostics import build_eclass_diagnostics
from mia_dpp.semantic.eclass_resolution import (
    EclassEvidenceResolution,
    EclassResolutionReport,
    EclassRetrievalStatus,
    EclassScopeDecision,
    NO_ECLASS_MATCH,
    UNRESOLVED_ECLASS,
)
from mia_dpp.semantic.jev import ChoiceDecision
from mia_dpp.semantic.models import ContextScope
from mia_dpp.semantic.open_property import (
    OpenPropertyConflictKind,
    OpenPropertyDisposition,
    build_open_property_proposals,
)
from mia_dpp.tools.mapping.targets import TECHNICAL_DATA_ARBITRARY_PROPERTY_PATH


def _record(
    identifier: str,
    label: str,
    value: str,
    context: tuple[str, ...],
) -> EvidenceRecord:
    return EvidenceRecord(
        id=identifier,
        predicate=f"source.{identifier}",
        source_label=label,
        value=value,
        context_path=context,
        source_uri="https://manufacturer.example/robot",
        source_content_sha256=hashlib.sha256(b"fixture").hexdigest(),
        source_location=SourceLocation(excerpt=f"{label}: {value}"),
        extraction_method="fixture",
        extractor_name="fixture",
        extractor_version="1",
        status=EvidenceStatus.OBSERVED,
        acquired_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _package(*records: EvidenceRecord) -> ProductKnowledgePackage:
    return ProductKnowledgePackage(
        product_id="robot-1",
        product_name="Robot",
        evidence=records,
    )


def _scope_decision(
    *,
    scope: ContextScope,
    choice: str,
    candidates: tuple[EclassProperty, ...],
    winner: float = 0.90,
) -> EclassScopeDecision:
    probabilities = {item.irdi: 0.0 for item in candidates}
    probabilities[NO_ECLASS_MATCH] = 0.0
    probabilities[UNRESOLVED_ECLASS] = 0.0
    probabilities[choice] = winner
    remainder = 1.0 - winner
    alternatives = [key for key in probabilities if key != choice]
    share = remainder / len(alternatives)
    for key in alternatives:
        probabilities[key] = share
    return EclassScopeDecision(
        scope=scope,
        context_view_id=f"ctx-{scope.value}",
        decision=ChoiceDecision(
            question_id="eclass_property",
            choice=choice,
            probabilities=probabilities,
        ),
    )


def _result(
    *,
    evidence_id: str,
    candidates: tuple[EclassProperty, ...],
    sibling_choice: str,
    product_choice: str | None = None,
) -> EclassEvidenceResolution:
    return EclassEvidenceResolution(
        evidence_id=evidence_id,
        applicable=True,
        search_query="fixture",
        retrieval_status=EclassRetrievalStatus.CANDIDATES_VERIFIED,
        verified_candidates=candidates,
        decisions=(
            _scope_decision(
                scope=ContextScope.SIBLINGS,
                choice=sibling_choice,
                candidates=candidates,
            ),
            _scope_decision(
                scope=ContextScope.FULL_PRODUCT,
                choice=product_choice or sibling_choice,
                candidates=candidates,
            ),
        ),
    )


def _build(
    package: ProductKnowledgePackage,
    results: tuple[EclassEvidenceResolution, ...],
):
    resolution = EclassResolutionReport(
        provider_name="fixture-eclass",
        results=results,
    )
    diagnostics = build_eclass_diagnostics(resolution)
    return build_open_property_proposals(
        package=package,
        normalization=normalize_package(package),
        eclass_resolution=resolution,
        eclass_diagnostics=diagnostics,
        policy_settings=DecisionPolicySettings(),
        templates=OfficialTemplateRepository(),
    )


def test_verified_consensus_builds_deterministic_arbitrary_property_target() -> None:
    concept = EclassProperty(
        irdi="0173-1#02-AAO677#001",
        preferred_name="Position repeatability",
        unit_symbol="mm",
    )
    package = _package(
        _record(
            "ev-1",
            "Repeat accuracy",
            "0.03 mm",
            ("Technical Specifications", "Motion Performance"),
        )
    )

    report = _build(
        package,
        (
            _result(
                evidence_id="ev-1",
                candidates=(concept,),
                sibling_choice=concept.irdi,
            ),
        ),
    )

    proposal = report.proposals[0]
    assert proposal.disposition is OpenPropertyDisposition.PROPOSED
    assert proposal.review_priority is DecisionPriority.AUTO
    assert proposal.eclass_property == concept
    assert proposal.target is not None
    assert proposal.target.template_key == "technical_data"
    assert proposal.target.template_path == TECHNICAL_DATA_ARBITRARY_PROPERTY_PATH
    assert proposal.target.id_short == "Position_repeatability"
    assert proposal.target.instance_path == (
        "TechnicalData",
        "TechnicalPropertyAreas",
        "[]",
        "Position_repeatability",
    )
    assert proposal.target.semantic_id.primary_value == concept.irdi
    assert len(proposal.target.list_instance_bindings) == 1
    area = proposal.target.list_instance_bindings[0]
    assert area.template_path == (
        "TechnicalData",
        "TechnicalPropertyAreas",
        "[]",
    )
    assert area.source_context_path == (
        "Technical Specifications",
        "Motion Performance",
    )
    assert area.label == "Motion Performance"
    assert proposal.semantic_slot is not None
    assert proposal.semantic_slot.context_path == (
        "Technical Specifications",
        "Motion Performance",
    )
    assert report.conflicts == ()


def test_same_semantic_slot_with_different_values_is_value_conflict() -> None:
    concept = EclassProperty(
        irdi="0173-1#02-AAO677#001",
        preferred_name="Position repeatability",
    )
    context = ("Technical Specifications", "Motion Performance")
    package = _package(
        _record("ev-1", "Repeat accuracy", "0.03 mm", context),
        _record("ev-2", "Repeat accuracy", "0.05 mm", context),
    )

    report = _build(
        package,
        (
            _result(
                evidence_id="ev-1",
                candidates=(concept,),
                sibling_choice=concept.irdi,
            ),
            _result(
                evidence_id="ev-2",
                candidates=(concept,),
                sibling_choice=concept.irdi,
            ),
        ),
    )

    conflicts = [
        item
        for item in report.conflicts
        if item.kind is OpenPropertyConflictKind.VALUE_CONFLICT
    ]
    assert len(conflicts) == 1
    assert {conflicts[0].left_evidence_id, conflicts[0].right_evidence_id} == {
        "ev-1",
        "ev-2",
    }


def test_equivalent_normalized_values_do_not_conflict() -> None:
    concept = EclassProperty(
        irdi="0173-1#02-AAO677#001",
        preferred_name="Position repeatability",
    )
    context = ("Technical Specifications", "Motion Performance")
    package = _package(
        _record("ev-1", "Repeat accuracy", "0.03 mm", context),
        _record("ev-2", "Repeat accuracy", "0,03 mm", context),
    )

    report = _build(
        package,
        (
            _result(
                evidence_id="ev-1",
                candidates=(concept,),
                sibling_choice=concept.irdi,
            ),
            _result(
                evidence_id="ev-2",
                candidates=(concept,),
                sibling_choice=concept.irdi,
            ),
        ),
    )

    assert not any(
        item.kind is OpenPropertyConflictKind.VALUE_CONFLICT
        for item in report.conflicts
    )
    assert any(
        item.kind is OpenPropertyConflictKind.REDUNDANT_DUPLICATE
        for item in report.conflicts
    )


def test_different_concepts_with_same_sanitized_name_are_id_short_collision() -> None:
    concept_a = EclassProperty(
        irdi="irdi-A",
        preferred_name="Power / rating",
    )
    concept_b = EclassProperty(
        irdi="irdi-B",
        preferred_name="Power - rating",
    )
    context = ("Technical Specifications", "Electrical")
    package = _package(
        _record("ev-1", "Power A", "500 W", context),
        _record("ev-2", "Power B", "600 W", context),
    )

    report = _build(
        package,
        (
            _result(
                evidence_id="ev-1",
                candidates=(concept_a,),
                sibling_choice=concept_a.irdi,
            ),
            _result(
                evidence_id="ev-2",
                candidates=(concept_b,),
                sibling_choice=concept_b.irdi,
            ),
        ),
    )

    conflicts = [
        item
        for item in report.conflicts
        if item.kind is OpenPropertyConflictKind.ID_SHORT_COLLISION
    ]
    assert len(conflicts) == 1
    assert conflicts[0].id_short == "Power_rating"


def test_same_concept_in_different_component_contexts_get_distinct_area_bindings() -> None:
    concept = EclassProperty(
        irdi="0173-1#02-POWER#001",
        preferred_name="Rated power",
    )
    package = _package(
        _record(
            "ev-1",
            "Rated power",
            "500 W",
            ("Technical Specifications", "Motor A"),
        ),
        _record(
            "ev-2",
            "Rated power",
            "700 W",
            ("Technical Specifications", "Motor B"),
        ),
    )

    report = _build(
        package,
        (
            _result(
                evidence_id="ev-1",
                candidates=(concept,),
                sibling_choice=concept.irdi,
            ),
            _result(
                evidence_id="ev-2",
                candidates=(concept,),
                sibling_choice=concept.irdi,
            ),
        ),
    )

    proposals = {
        item.evidence_id: item
        for item in report.proposals
    }
    left = proposals["ev-1"]
    right = proposals["ev-2"]
    assert left.target is not None
    assert right.target is not None
    assert left.target.instance_path == right.target.instance_path
    assert left.target.projection_identity != right.target.projection_identity
    assert (
        left.target.list_instance_bindings[0].instance_key
        != right.target.list_instance_bindings[0].instance_key
    )
    assert report.conflicts == ()


def test_strong_eclass_scope_disagreement_does_not_create_target() -> None:
    concept_a = EclassProperty(
        irdi="irdi-A",
        preferred_name="Position repeatability",
    )
    concept_b = EclassProperty(
        irdi="irdi-B",
        preferred_name="Positioning accuracy",
    )
    package = _package(
        _record(
            "ev-1",
            "Repeat accuracy",
            "0.03 mm",
            ("Technical Specifications", "Motion"),
        )
    )

    report = _build(
        package,
        (
            _result(
                evidence_id="ev-1",
                candidates=(concept_a, concept_b),
                sibling_choice=concept_a.irdi,
                product_choice=concept_b.irdi,
            ),
        ),
    )

    proposal = report.proposals[0]
    assert proposal.disposition is OpenPropertyDisposition.CLASSIFICATION_ALARM
    assert proposal.review_priority is DecisionPriority.ALARM
    assert proposal.target is None
    assert proposal.eclass_property is None
    assert proposal.semantic_slot is None

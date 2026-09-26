"""Authoritative promotion tests for verified open Technical Properties."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime

import pytest
from aas_core3 import jsonization, verification

from mia_dpp.aas.build import build_dpp
from mia_dpp.aas.requirements import build_template_index
from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.evidence import (
    EvidenceRecord,
    EvidenceStatus,
    ProductKnowledgePackage,
    SourceLocation,
)
from mia_dpp.domain.mappings import (
    EvidenceOutcome,
    EvidenceOutcomeStatus,
    MappingOrigin,
    MappingResult,
    MappingStatus,
)
from mia_dpp.normalization import normalize_package
from mia_dpp.semantic import ContextScope, build_context_views
from mia_dpp.semantic.decision_policy import DecisionPolicySettings, DecisionPriority
from mia_dpp.semantic.eclass import EclassProperty, EclassSearchCandidate
from mia_dpp.semantic.eclass_diagnostics import (
    EclassDiagnosticsReport,
    EclassEvidenceDiagnostics,
    build_eclass_diagnostics,
)
from mia_dpp.semantic.eclass_resolution import (
    EclassEvidenceResolution,
    EclassResolutionReport,
    EclassRetrievalStatus,
    resolve_eclass_for_technical_properties,
)
from mia_dpp.semantic.idta_routing import route_views
from mia_dpp.semantic.jev import ChoiceDecision
from mia_dpp.semantic.open_property import (
    OpenPropertyConflict,
    OpenPropertyConflictKind,
    OpenPropertyDisposition,
    OpenPropertyProposal,
    OpenPropertyProposalReport,
    SemanticSlotIdentity,
    build_open_property_proposals,
    technical_property_area_binding,
)
from mia_dpp.semantic.promotion import promote_open_properties
from mia_dpp.tools.mapping.review import MappingReviewService
from mia_dpp.tools.mapping.targets import (
    TECHNICAL_DATA_ARBITRARY_PROPERTY_PATH,
    mapping_target,
)
from mia_dpp.tools.mapping.text_mapping import propose_text_mappings


def _record(
    identifier: str,
    *,
    label: str = "Rated power",
    value: str = "500",
    context: tuple[str, ...] = ("Technical Specifications", "Motor A"),
) -> EvidenceRecord:
    return EvidenceRecord(
        id=identifier,
        predicate=f"source.{identifier}",
        source_label=label,
        value=value,
        unit="W",
        context_path=context,
        source_uri="urn:test:promotion",
        source_content_sha256=hashlib.sha256(b"fixture").hexdigest(),
        source_location=SourceLocation(excerpt=f"{label}: {value} W"),
        extraction_method="test",
        extractor_name="test",
        extractor_version="1",
        status=EvidenceStatus.OBSERVED,
        acquired_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _base_mapping(*records: EvidenceRecord) -> MappingResult:
    return MappingResult(
        unmatched_evidence_ids=tuple(record.id for record in records),
        outcomes=tuple(
            EvidenceOutcome(
                evidence_id=record.id,
                status=EvidenceOutcomeStatus.UNMAPPED,
                reason="Legacy mapper found no fixed target.",
                mapping_origin=MappingOrigin.SEMANTIC_AGENT,
            )
            for record in records
        ),
    )


def _target(
    repository: OfficialTemplateRepository,
    record: EvidenceRecord,
    concept: EclassProperty,
):
    return mapping_target(
        repository.load("technical_data"),
        TECHNICAL_DATA_ARBITRARY_PROPERTY_PATH,
        id_short=concept.preferred_name.replace(" ", "_"),
        semantic_id=concept.irdi,
        list_instance_bindings=(technical_property_area_binding(record.context_path),),
    )


def _inputs(
    *,
    priority: DecisionPriority,
    disposition: OpenPropertyDisposition = OpenPropertyDisposition.PROPOSED,
    conflict: bool = False,
):
    repository = OfficialTemplateRepository()
    record = _record("ev-1")
    package = ProductKnowledgePackage(
        product_id="robot-1",
        product_name="Robot",
        evidence=(record,),
    )
    concept_a = EclassProperty(
        irdi="0173-1#02-POWER#001",
        preferred_name="RatedPower",
    )
    concept_b = EclassProperty(
        irdi="0173-1#02-POWER-ALT#001",
        preferred_name="AlternativePower",
    )
    target = (
        _target(repository, record, concept_a)
        if disposition is OpenPropertyDisposition.PROPOSED
        else None
    )
    proposal = OpenPropertyProposal(
        evidence_id=record.id,
        disposition=disposition,
        reason="fixture semantic result",
        review_priority=priority,
        source_field="Rated power",
        source_value="500 W",
        normalized_value_fingerprint="a" * 64 if target is not None else None,
        eclass_property=concept_a if target is not None else None,
        property_area=(
            technical_property_area_binding(record.context_path) if target is not None else None
        ),
        semantic_slot=(
            None
            if target is None
            else SemanticSlotIdentity(
                key="slot-" + "a" * 24,
                template_key="technical_data",
                template_release=repository.load("technical_data").release.release,
                template_path=TECHNICAL_DATA_ARBITRARY_PROPERTY_PATH,
                semantic_id=concept_a.irdi,
                context_path=record.context_path,
                context_key="context-" + "b" * 24,
            )
        ),
        target=target,
    )
    conflicts = (
        (
            OpenPropertyConflict(
                kind=OpenPropertyConflictKind.VALUE_CONFLICT,
                left_evidence_id=record.id,
                right_evidence_id=record.id,
                semantic_slot_key="slot-" + "a" * 24,
                reason="fixture conflict",
            ),
        )
        if conflict
        else ()
    )
    resolution = EclassResolutionReport(
        provider_name="fixture",
        results=(
            EclassEvidenceResolution(
                evidence_id=record.id,
                applicable=True,
                search_query="Rated power",
                retrieval_status=EclassRetrievalStatus.CANDIDATES_VERIFIED,
                verified_candidates=(concept_a, concept_b),
            ),
        ),
    )
    diagnostics = EclassDiagnosticsReport(
        evidence=(
            EclassEvidenceDiagnostics(
                evidence_id=record.id,
                retrieval_status=EclassRetrievalStatus.CANDIDATES_VERIFIED,
                consensus_choice=concept_a.irdi,
                consensus_count=2,
                scope_count=2,
                scope_agreement=1.0,
                distinct_choice_count=1,
                minimum_selected_probability=0.9,
                minimum_margin=0.8,
                maximum_runner_up_ratio=0.1,
                maximum_normalized_entropy=0.2,
            ),
        ),
    )
    return (
        repository,
        package,
        record,
        concept_a,
        concept_b,
        OpenPropertyProposalReport(proposals=(proposal,), conflicts=conflicts),
        resolution,
        diagnostics,
    )


def test_conflict_free_auto_is_promoted_without_review() -> None:
    repository, package, record, _, _, proposals, resolution, diagnostics = _inputs(
        priority=DecisionPriority.AUTO
    )

    promoted = promote_open_properties(
        package=package,
        mapping=_base_mapping(record),
        existing_review_items=(),
        proposals=proposals,
        eclass_resolution=resolution,
        eclass_diagnostics=diagnostics,
        templates=repository,
        cycle_seed="cycle-test",
    )

    assert promoted.review_items == ()
    assert promoted.report.promoted_evidence_ids == (record.id,)
    assert promoted.mapping.unmatched_evidence_ids == ()
    assert len(promoted.mapping.mapped) == 1
    mapping = promoted.mapping.mapped[0]
    assert mapping.status is MappingStatus.AUTO
    assert mapping.mapping_origin is MappingOrigin.SEMANTIC_ENGINE
    outcome = promoted.mapping.outcomes[0]
    assert outcome.status is EvidenceOutcomeStatus.MAPPED
    assert outcome.direct_target is True
    assert outcome.requirement_id is None


def test_confirm_becomes_direct_review_with_verified_alternatives() -> None:
    repository, package, record, concept_a, concept_b, proposals, resolution, diagnostics = _inputs(
        priority=DecisionPriority.CONFIRM
    )

    promoted = promote_open_properties(
        package=package,
        mapping=_base_mapping(record),
        existing_review_items=(),
        proposals=proposals,
        eclass_resolution=resolution,
        eclass_diagnostics=diagnostics,
        templates=repository,
        cycle_seed="cycle-test",
    )

    assert promoted.report.review_evidence_ids == (record.id,)
    assert len(promoted.review_items) == 1
    review = promoted.review_items[0]
    assert review.target_kind == "direct"
    assert review.review_priority == "confirm"
    assert review.mapping is not None
    assert {item.semantic_id.primary_value for item in review.alternative_targets} == {
        concept_a.irdi,
        concept_b.irdi,
    }
    assert promoted.mapping.ambiguous[0].evidence_id == record.id


def test_alarm_has_no_preselected_mapping_but_keeps_verified_alternatives() -> None:
    repository, package, record, concept_a, concept_b, proposals, resolution, diagnostics = _inputs(
        priority=DecisionPriority.ALARM,
        disposition=OpenPropertyDisposition.CLASSIFICATION_ALARM,
    )

    promoted = promote_open_properties(
        package=package,
        mapping=_base_mapping(record),
        existing_review_items=(),
        proposals=proposals,
        eclass_resolution=resolution,
        eclass_diagnostics=diagnostics,
        templates=repository,
        cycle_seed="cycle-test",
    )

    review = promoted.review_items[0]
    assert review.review_priority == "alarm"
    assert review.mapping is None
    assert {item.semantic_id.primary_value for item in review.alternative_targets} == {
        concept_a.irdi,
        concept_b.irdi,
    }
    outcome = promoted.mapping.outcomes[0]
    assert outcome.status is EvidenceOutcomeStatus.UNCERTAIN
    assert outcome.direct_target is False


def test_optional_stays_unmapped_under_conservative_policy() -> None:
    repository, package, record, _, _, proposals, resolution, diagnostics = _inputs(
        priority=DecisionPriority.OPTIONAL
    )

    promoted = promote_open_properties(
        package=package,
        mapping=_base_mapping(record),
        existing_review_items=(),
        proposals=proposals,
        eclass_resolution=resolution,
        eclass_diagnostics=diagnostics,
        templates=repository,
        cycle_seed="cycle-test",
    )

    assert promoted.review_items == ()
    assert promoted.mapping.mapped == ()
    assert promoted.mapping.unmatched_evidence_ids == (record.id,)
    assert promoted.report.unmapped_evidence_ids == (record.id,)


def test_conflict_forces_review_even_when_policy_was_auto() -> None:
    repository, package, record, _, _, proposals, resolution, diagnostics = _inputs(
        priority=DecisionPriority.AUTO,
        conflict=True,
    )

    promoted = promote_open_properties(
        package=package,
        mapping=_base_mapping(record),
        existing_review_items=(),
        proposals=proposals,
        eclass_resolution=resolution,
        eclass_diagnostics=diagnostics,
        templates=repository,
        cycle_seed="cycle-test",
    )

    assert promoted.review_items[0].review_priority == "alarm"
    assert promoted.report.conflict_evidence_ids == (record.id,)


def test_human_can_keep_or_select_verified_direct_target() -> None:
    repository, package, record, concept_a, concept_b, proposals, resolution, diagnostics = _inputs(
        priority=DecisionPriority.CONFIRM
    )
    promoted = promote_open_properties(
        package=package,
        mapping=_base_mapping(record),
        existing_review_items=(),
        proposals=proposals,
        eclass_resolution=resolution,
        eclass_diagnostics=diagnostics,
        templates=repository,
        cycle_seed="cycle-test",
    )
    service = MappingReviewService(repository)
    index = build_template_index(
        (repository.load("digital_nameplate"), repository.load("technical_data"))
    )
    review = promoted.review_items[0]

    _, kept, kept_item = service.decide(
        package,
        promoted.mapping,
        index,
        review,
        decision="keep",
        thread_id="thread-12345678",
        actor_name="Reviewer",
    )
    assert kept_item.status is EvidenceOutcomeStatus.MAPPED
    assert kept.mapped[0].human_reviewed is True
    assert kept.mapped[0].target.semantic_id.primary_value == concept_a.irdi

    _, changed, changed_item = service.decide(
        package,
        promoted.mapping,
        index,
        review,
        decision="change_target",
        thread_id="thread-12345678",
        corrected_semantic_id=concept_b.irdi,
        actor_name="Reviewer",
    )
    assert changed_item.status is EvidenceOutcomeStatus.MAPPED
    assert changed.mapped[0].target.semantic_id.primary_value == concept_b.irdi
    assert changed.mapped[0].mapping_origin is MappingOrigin.HUMAN


def test_projection_uniqueness_rejects_unresolved_duplicate_targets() -> None:
    repository, package, record, _, _, proposals, resolution, diagnostics = _inputs(
        priority=DecisionPriority.AUTO
    )
    promoted = promote_open_properties(
        package=package,
        mapping=_base_mapping(record),
        existing_review_items=(),
        proposals=proposals,
        eclass_resolution=resolution,
        eclass_diagnostics=diagnostics,
        templates=repository,
        cycle_seed="cycle-test",
    )
    mapping = promoted.mapping.mapped[0]
    duplicate_record = _record("ev-2")
    duplicate = mapping.model_copy(
        update={
            "id": "mapping-duplicate",
            "evidence_id": duplicate_record.id,
        }
    )
    duplicate_outcome = EvidenceOutcome(
        evidence_id=duplicate_record.id,
        status=EvidenceOutcomeStatus.MAPPED,
        direct_target=True,
        reason="duplicate fixture",
        mapping_origin=MappingOrigin.SEMANTIC_ENGINE,
    )
    result = MappingResult(
        mapped=(mapping, duplicate),
        outcomes=(
            promoted.mapping.outcomes[0],
            duplicate_outcome,
        ),
    )

    with pytest.raises(ValueError, match="duplicate projection targets"):
        MappingReviewService.validate_projection_uniqueness(result)


def test_promoted_technical_property_reaches_final_technical_data_submodel() -> None:
    repository, package, record, concept_a, _, proposals, resolution, diagnostics = _inputs(
        priority=DecisionPriority.AUTO
    )
    promoted = promote_open_properties(
        package=package,
        mapping=_base_mapping(record),
        existing_review_items=(),
        proposals=proposals,
        eclass_resolution=resolution,
        eclass_diagnostics=diagnostics,
        templates=repository,
        cycle_seed="cycle-end-to-end",
    )
    assert len(promoted.mapping.mapped) == 1

    nameplate = propose_text_mappings(
        (
            "AFRISO gauge, model RF100-16, serial number 2024-8871, built 2024, "
            "IP65, 0-16 bar, material number 63820."
        ),
        repository,
    )
    approved_nameplate = [
        mapping.model_copy(
            update={
                "id": f"mapping-nameplate-{index}",
                "status": MappingStatus.APPROVED,
            }
        )
        for index, mapping in enumerate(nameplate.mappings)
    ]
    dpp = build_dpp(
        nameplate.product_name,
        [*approved_nameplate, *promoted.mapping.mapped],
        repository=repository,
        now=datetime(2026, 1, 2, tzinfo=UTC),
    )

    environment = jsonization.environment_from_jsonable(dpp.environment)
    assert list(verification.verify(environment)) == []
    technical = next(
        item for item in dpp.environment["submodels"] if item["idShort"] == "TechnicalData"
    )
    areas = next(
        item
        for item in technical["submodelElements"]
        if item.get("idShort") == "TechnicalPropertyAreas"
    )
    rendered = str(areas)
    assert "RatedPower" in rendered
    assert concept_a.irdi in rendered
    assert "500" in rendered


def test_evidence_to_verified_semantics_to_final_technical_data_is_end_to_end() -> None:
    class RoutingDecider:
        async def choose(
            self,
            *,
            question_id: str,
            state: Mapping[str, object],
            instructions: str,
            criteria: Mapping[str, str],
        ) -> ChoiceDecision:
            del state, instructions
            choice = "technical_data|TechnicalData/TechnicalPropertyAreas/[]/ArbitraryProperty"
            assert choice in criteria
            probabilities = dict.fromkeys(criteria, 0.0)
            probabilities[choice] = 1.0
            return ChoiceDecision(
                question_id=question_id,
                choice=choice,
                probabilities=probabilities,
            )

    class EclassDecider:
        async def choose(
            self,
            *,
            question_id: str,
            state: Mapping[str, object],
            instructions: str,
            criteria: Mapping[str, str],
        ) -> ChoiceDecision:
            del state, instructions
            choice = "0173-1#02-POWER#001"
            assert choice in criteria
            probabilities = dict.fromkeys(criteria, 0.0)
            probabilities[choice] = 1.0
            return ChoiceDecision(
                question_id=question_id,
                choice=choice,
                probabilities=probabilities,
            )

    class EclassProvider:
        provider_name = "fixture-eclass"

        async def search_properties(
            self,
            query: str,
            *,
            limit: int,
        ) -> tuple[EclassSearchCandidate, ...]:
            assert query == "Rated power"
            assert limit == 12
            return (
                EclassSearchCandidate(
                    irdi="0173-1#02-POWER#001",
                    preferred_name="RatedPower",
                    definition="Rated power of the technical object.",
                ),
            )

        async def get_property(self, irdi: str) -> EclassProperty | None:
            if irdi != "0173-1#02-POWER#001":
                return None
            return EclassProperty(
                irdi=irdi,
                preferred_name="RatedPower",
                definition="Rated power of the technical object.",
                data_type="REAL_MEASURE",
                unit_symbol="W",
                release="15.0",
            )

    async def run_pipeline() -> None:
        repository = OfficialTemplateRepository()
        record = _record("ev-end-to-end", context=("Technical Specifications", "Motor A"))
        package = ProductKnowledgePackage(
            product_id="robot-end-to-end",
            product_name="Industrial Robot",
            evidence=(record,),
        )
        normalization = normalize_package(package)
        normalized = normalization.evidence[0]
        assert normalized.value.number == 500.0
        assert normalized.value.unit == "W"
        assert record.value == "500"

        contexts = build_context_views(package, normalization)
        scopes = tuple(ContextScope)
        routing = await route_views(
            decider=RoutingDecider(),
            repository=repository,
            selected_template_keys=("digital_nameplate", "technical_data"),
            package=package,
            normalization=normalization,
            context_views=contexts,
            scopes=scopes,
        )
        assert routing.traces
        assert all(trace.selected_template_key == "technical_data" for trace in routing.traces)
        assert all(trace.terminal_reason == "wildcard" for trace in routing.traces)

        resolution = await resolve_eclass_for_technical_properties(
            provider=EclassProvider(),
            decider=EclassDecider(),
            package=package,
            normalization=normalization,
            context_views=contexts,
            routing=routing,
            scopes=scopes,
        )
        diagnostics = build_eclass_diagnostics(resolution)
        proposal_report = build_open_property_proposals(
            package=package,
            normalization=normalization,
            eclass_resolution=resolution,
            eclass_diagnostics=diagnostics,
            policy_settings=DecisionPolicySettings(),
            templates=repository,
        )
        proposal = proposal_report.proposals[0]
        assert proposal.review_priority is DecisionPriority.AUTO
        assert proposal.eclass_property is not None
        assert proposal.eclass_property.irdi == "0173-1#02-POWER#001"

        promoted = promote_open_properties(
            package=package,
            mapping=_base_mapping(record),
            existing_review_items=(),
            proposals=proposal_report,
            eclass_resolution=resolution,
            eclass_diagnostics=diagnostics,
            templates=repository,
            cycle_seed="cycle-integrated-semantic-path",
        )
        assert promoted.report.promoted_evidence_ids == (record.id,)
        assert promoted.mapping.mapped[0].mapping_origin is MappingOrigin.SEMANTIC_ENGINE
        assert promoted.mapping.mapped[0].target.list_instance_bindings
        assert promoted.mapping.mapped[0].target.list_instance_bindings[0].source_context_path == (
            "Technical Specifications",
            "Motor A",
        )

        nameplate = propose_text_mappings(
            (
                "AFRISO gauge, model RF100-16, serial number 2024-8871, built 2024, "
                "IP65, 0-16 bar, material number 63820."
            ),
            repository,
        )
        approved_nameplate = [
            mapping.model_copy(
                update={
                    "id": f"mapping-integrated-nameplate-{index}",
                    "status": MappingStatus.APPROVED,
                }
            )
            for index, mapping in enumerate(nameplate.mappings)
        ]
        dpp = build_dpp(
            nameplate.product_name,
            [*approved_nameplate, *promoted.mapping.mapped],
            repository=repository,
            now=datetime(2026, 1, 2, tzinfo=UTC),
        )
        environment = jsonization.environment_from_jsonable(dpp.environment)
        assert list(verification.verify(environment)) == []
        technical = next(
            item for item in dpp.environment["submodels"] if item["idShort"] == "TechnicalData"
        )
        rendered = str(technical["submodelElements"])
        assert "TechnicalPropertyAreas" in rendered
        assert "RatedPower" in rendered
        assert "0173-1#02-POWER#001" in rendered
        assert "500" in rendered

    asyncio.run(run_pipeline())

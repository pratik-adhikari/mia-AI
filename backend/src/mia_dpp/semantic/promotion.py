"""Promote verified Jev/ECLASS open-property decisions into trusted mapping state."""

from __future__ import annotations

import hashlib
from mia_dpp.aas.identifiers import sanitize_id_short
from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.base import WireModel
from mia_dpp.domain.evidence import EvidenceRecord, ProductKnowledgePackage
from mia_dpp.domain.mappings import (
    EvidenceOutcome,
    EvidenceOutcomeStatus,
    FieldMapping,
    MappingAssessment,
    MappingBasis,
    MappingOrigin,
    MappingResult,
    MappingStatus,
    MappingTarget,
    SemanticReviewItem,
)
from mia_dpp.semantic.decision_policy import DecisionPriority
from mia_dpp.semantic.eclass_diagnostics import EclassDiagnosticsReport
from mia_dpp.semantic.eclass_resolution import (
    EclassEvidenceResolution,
    EclassResolutionReport,
    EclassRetrievalStatus,
)
from mia_dpp.semantic.open_property import (
    OpenPropertyConflict,
    OpenPropertyDisposition,
    OpenPropertyProposalReport,
    technical_property_area_binding,
)
from mia_dpp.tools.mapping.targets import (
    TECHNICAL_DATA_ARBITRARY_PROPERTY_PATH,
    mapping_target,
)


class SemanticPromotionReport(WireModel):
    """Inspect how shadow semantic results changed authoritative mapping state."""

    promoted_evidence_ids: tuple[str, ...] = ()
    review_evidence_ids: tuple[str, ...] = ()
    unmapped_evidence_ids: tuple[str, ...] = ()
    untouched_evidence_ids: tuple[str, ...] = ()
    conflict_evidence_ids: tuple[str, ...] = ()


class SemanticPromotionResult(WireModel):
    mapping: MappingResult
    review_items: tuple[SemanticReviewItem, ...]
    report: SemanticPromotionReport


def _display_value(record: EvidenceRecord) -> str:
    value = str(record.value)
    return f"{value} {record.unit}" if record.unit else value


def _mapping_id(record: EvidenceRecord, target: MappingTarget) -> str:
    payload = "\0".join(
        (
            record.id,
            target.template_key,
            "/".join(target.template_path),
            target.semantic_id.primary_value,
            *(
                f"{binding.instance_key}:{'/'.join(binding.template_path)}"
                for binding in target.list_instance_bindings
            ),
        )
    )
    return "mapping-" + hashlib.sha256(payload.encode()).hexdigest()[:24]


def _review_id(cycle_seed: str, evidence_id: str) -> str:
    return "review-" + hashlib.sha256(
        f"{cycle_seed}\0{evidence_id}".encode()
    ).hexdigest()[:24]


def _semantic_mapping(
    record: EvidenceRecord,
    target: MappingTarget,
    *,
    review_required: bool,
    reason: str,
) -> FieldMapping:
    return FieldMapping(
        id=_mapping_id(record, target),
        evidence_id=record.id,
        source_field=record.source_label or record.predicate,
        source_value=_display_value(record),
        target=target,
        assessment=MappingAssessment(
            basis=MappingBasis.SEMANTIC,
            review_required=review_required,
            reason=reason,
            uncertainties=(
                ("Human confirmation is required.",)
                if review_required
                else ()
            ),
        ),
        reasoning=reason,
        status=MappingStatus.REVIEW if review_required else MappingStatus.AUTO,
        mapping_origin=MappingOrigin.SEMANTIC_ENGINE,
    )


def _candidate_targets(
    *,
    record: EvidenceRecord,
    resolution: EclassEvidenceResolution,
    templates: OfficialTemplateRepository,
) -> tuple[MappingTarget, ...]:
    if not resolution.verified_candidates:
        return ()
    template = templates.load("technical_data")
    binding = technical_property_area_binding(record.context_path)
    targets: list[MappingTarget] = []
    seen: set[str] = set()
    for candidate in resolution.verified_candidates:
        if candidate.irdi in seen:
            continue
        seen.add(candidate.irdi)
        targets.append(
            mapping_target(
                template,
                TECHNICAL_DATA_ARBITRARY_PROPERTY_PATH,
                id_short=sanitize_id_short(
                    candidate.preferred_name,
                    fallback="Property",
                ),
                semantic_id=candidate.irdi,
                list_instance_bindings=(binding,),
            )
        )
    return tuple(targets)


def _replace_with(
    mapping: MappingResult,
    *,
    record: EvidenceRecord,
    field_mapping: FieldMapping | None,
    status: EvidenceOutcomeStatus,
    reason: str,
) -> MappingResult:
    mapped = [
        item
        for item in mapping.mapped
        if item.evidence_id != record.id
    ]
    ambiguous = [
        item
        for item in mapping.ambiguous
        if item.evidence_id != record.id
    ]
    rejected = tuple(
        item
        for item in mapping.rejected
        if item.evidence_id != record.id
    )
    unmatched = [
        item
        for item in mapping.unmatched_evidence_ids
        if item != record.id
    ]
    irrelevant = tuple(
        item
        for item in mapping.irrelevant_evidence_ids
        if item != record.id
    )
    rejected_ids = tuple(
        item
        for item in mapping.rejected_evidence_ids
        if item != record.id
    )
    outcomes = [
        item
        for item in mapping.outcomes
        if item.evidence_id != record.id
    ]

    if field_mapping is not None:
        if status is EvidenceOutcomeStatus.MAPPED:
            mapped.append(field_mapping)
        elif status is EvidenceOutcomeStatus.UNCERTAIN:
            ambiguous.append(field_mapping)
    elif status is EvidenceOutcomeStatus.UNMAPPED:
        unmatched.append(record.id)

    outcomes.append(
        EvidenceOutcome(
            evidence_id=record.id,
            status=status,
            direct_target=field_mapping is not None,
            reason=reason,
            mapping_origin=MappingOrigin.SEMANTIC_ENGINE,
        )
    )
    return MappingResult(
        mapped=tuple(mapped),
        ambiguous=tuple(ambiguous),
        rejected=rejected,
        unmatched_evidence_ids=tuple(unmatched),
        irrelevant_evidence_ids=irrelevant,
        rejected_evidence_ids=rejected_ids,
        outcomes=tuple(outcomes),
    )

def _conflict_ids(
    conflicts: tuple[OpenPropertyConflict, ...],
) -> frozenset[str]:
    return frozenset(
        evidence_id
        for conflict in conflicts
        for evidence_id in (
            conflict.left_evidence_id,
            conflict.right_evidence_id,
        )
    )


def _fixed_review_rows(
    existing: tuple[SemanticReviewItem, ...],
) -> tuple[SemanticReviewItem, ...]:
    """Keep only genuinely unresolved fixed-target rows."""

    return tuple(
        item
        for item in existing
        if (
            item.status is EvidenceOutcomeStatus.UNCERTAIN
            or (
                item.mapping is not None
                and item.mapping.assessment.review_required
            )
        )
    )


def promote_open_properties(
    *,
    package: ProductKnowledgePackage,
    mapping: MappingResult,
    existing_review_items: tuple[SemanticReviewItem, ...],
    proposals: OpenPropertyProposalReport,
    eclass_resolution: EclassResolutionReport,
    eclass_diagnostics: EclassDiagnosticsReport,
    templates: OfficialTemplateRepository,
    cycle_seed: str,
) -> SemanticPromotionResult:
    """Promote only verified open-property semantics under conservative policy."""

    records = {item.id: item for item in package.evidence}
    proposal_by_id = {item.evidence_id: item for item in proposals.proposals}
    resolution_by_id = {
        item.evidence_id: item for item in eclass_resolution.results
    }
    diagnostics_by_id = {
        item.evidence_id: item for item in eclass_diagnostics.evidence
    }
    conflict_ids = _conflict_ids(proposals.conflicts)

    result = mapping
    review_items = list(_fixed_review_rows(existing_review_items))
    promoted: list[str] = []
    review_ids: list[str] = []
    unmapped: list[str] = []
    untouched: list[str] = []

    for record in package.evidence:
        proposal = proposal_by_id.get(record.id)
        resolution = resolution_by_id.get(record.id)
        diagnostics = diagnostics_by_id.get(record.id)
        if proposal is None or resolution is None or diagnostics is None:
            untouched.append(record.id)
            continue
        if not resolution.applicable:
            untouched.append(record.id)
            continue

        candidates = _candidate_targets(
            record=record,
            resolution=resolution,
            templates=templates,
        )
        conflict = record.id in conflict_ids

        if (
            proposal.disposition is OpenPropertyDisposition.PROPOSED
            and proposal.target is not None
            and proposal.review_priority is DecisionPriority.AUTO
            and not conflict
        ):
            field_mapping = _semantic_mapping(
                record,
                proposal.target,
                review_required=False,
                reason=(
                    "Verified ECLASS concept and IDTA wildcard route were strong and "
                    "stable across configured context scopes."
                ),
            )
            result = _replace_with(
                result,
                record=record,
                field_mapping=field_mapping,
                status=EvidenceOutcomeStatus.MAPPED,
                reason="Promoted conflict-free AUTO open Technical Property.",
            )
            promoted.append(record.id)
            continue

        requires_review = (
            conflict
            or proposal.review_priority in {
                DecisionPriority.CONFIRM,
                DecisionPriority.ALARM,
            }
            or proposal.disposition
            in {
                OpenPropertyDisposition.CLASSIFICATION_ALARM,
                OpenPropertyDisposition.UNRESOLVED,
            }
        )
        if requires_review and candidates:
            selected = (
                _semantic_mapping(
                    record,
                    proposal.target,
                    review_required=True,
                    reason=(
                        "Verified semantic target requires human confirmation before "
                        "authoritative AAS projection."
                    ),
                )
                if proposal.target is not None
                else None
            )
            result = _replace_with(
                result,
                record=record,
                field_mapping=selected,
                status=EvidenceOutcomeStatus.UNCERTAIN,
                reason=(
                    "Open Technical Property requires human semantic confirmation."
                    if not conflict
                    else "Open Technical Property has a wildcard conflict requiring human review."
                ),
            )
            review_items.append(
                SemanticReviewItem(
                    id=_review_id(cycle_seed, record.id),
                    evidence_id=record.id,
                    status=EvidenceOutcomeStatus.UNCERTAIN,
                    target_kind="direct",
                    alternative_targets=candidates,
                    review_priority=(
                        "alarm"
                        if conflict
                        or proposal.review_priority is DecisionPriority.ALARM
                        else "confirm"
                    ),
                    reason=(
                        "Verified ECLASS candidates require human selection."
                        if selected is None
                        else "Verify the proposed ECLASS concept and TechnicalPropertyArea."
                    ),
                    mapping=selected,
                )
            )
            review_ids.append(record.id)
            continue

        # OPTIONAL is deliberately non-authoritative and non-blocking under the
        # conservative promotion policy. Retrieval/no-match failures also remain unmapped.
        result = _replace_with(
            result,
            record=record,
            field_mapping=None,
            status=EvidenceOutcomeStatus.UNMAPPED,
            reason=(
                "Semantic result was not strong enough for automatic promotion."
                if proposal.review_priority is DecisionPriority.OPTIONAL
                else proposal.reason
            ),
        )
        unmapped.append(record.id)

    review_by_evidence: dict[str, SemanticReviewItem] = {}
    for item in review_items:
        review_by_evidence[item.evidence_id] = item

    return SemanticPromotionResult(
        mapping=result,
        review_items=tuple(review_by_evidence.values()),
        report=SemanticPromotionReport(
            promoted_evidence_ids=tuple(promoted),
            review_evidence_ids=tuple(review_ids),
            unmapped_evidence_ids=tuple(unmapped),
            untouched_evidence_ids=tuple(untouched),
            conflict_evidence_ids=tuple(sorted(conflict_ids)),
        ),
    )

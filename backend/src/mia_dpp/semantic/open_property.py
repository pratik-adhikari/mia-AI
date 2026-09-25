"""Shadow proposals for open Technical Data ArbitraryProperty mappings."""

from __future__ import annotations

from collections import defaultdict
from enum import StrEnum

from pydantic import Field, model_validator

from mia_dpp.aas.identifiers import sanitize_id_short
from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.canonical import sha256_json
from mia_dpp.domain.base import WireModel
from mia_dpp.domain.evidence import EvidenceRecord, ProductKnowledgePackage
from mia_dpp.domain.mappings import ListInstanceBinding, MappingTarget
from mia_dpp.normalization.models import NormalizationReport, NormalizedEvidence
from mia_dpp.semantic.decision_policy import DecisionPolicySettings, DecisionPriority
from mia_dpp.semantic.eclass import EclassProperty
from mia_dpp.semantic.eclass_diagnostics import (
    EclassDiagnosticsReport,
    EclassEvidenceDiagnostics,
    classify_eclass_diagnostics,
)
from mia_dpp.semantic.eclass_resolution import (
    NO_ECLASS_MATCH,
    UNRESOLVED_ECLASS,
    EclassEvidenceResolution,
    EclassResolutionReport,
    EclassRetrievalStatus,
)
from mia_dpp.tools.mapping.targets import (
    TECHNICAL_DATA_ARBITRARY_PROPERTY_PATH,
    TECHNICAL_PROPERTY_AREA_LIST_PATH,
    mapping_target,
)


class OpenPropertyDisposition(StrEnum):
    PROPOSED = "proposed"
    NOT_APPLICABLE = "not_applicable"
    RETRIEVAL_EMPTY = "retrieval_empty"
    NO_VERIFIED_CANDIDATES = "no_verified_candidates"
    NO_ECLASS_MATCH = "no_eclass_match"
    UNRESOLVED = "unresolved"
    CLASSIFICATION_ALARM = "classification_alarm"


class OpenPropertyConflictKind(StrEnum):
    VALUE_CONFLICT = "value_conflict"
    REDUNDANT_DUPLICATE = "redundant_duplicate"
    ID_SHORT_COLLISION = "id_short_collision"
    PROJECTION_CONTEXT_COLLISION = "projection_context_collision"


def technical_property_area_binding(
    context_path: tuple[str, ...],
) -> ListInstanceBinding:
    """Derive one stable TechnicalPropertyArea instance from preserved source hierarchy."""

    label = context_path[-1] if context_path else "Technical Properties"
    payload = {
        "templatePath": TECHNICAL_PROPERTY_AREA_LIST_PATH,
        "sourceContextPath": context_path,
    }
    return ListInstanceBinding(
        template_path=TECHNICAL_PROPERTY_AREA_LIST_PATH,
        instance_key="list-instance-" + sha256_json(payload)[:24],
        source_context_path=context_path,
        label=label,
    )


class SemanticSlotIdentity(WireModel):
    """Context-aware identity for an open semantic property slot."""

    key: str = Field(pattern=r"^slot-[0-9a-f]{24}$")
    template_key: str
    template_release: str
    template_path: tuple[str, ...] = Field(min_length=1)
    semantic_id: str = Field(min_length=1)
    context_path: tuple[str, ...]
    context_key: str = Field(pattern=r"^context-[0-9a-f]{24}$")


class OpenPropertyProposal(WireModel):
    """One non-authoritative candidate mapping into an IDTA wildcard target."""

    evidence_id: str = Field(min_length=1)
    disposition: OpenPropertyDisposition
    reason: str = Field(min_length=1)
    review_priority: DecisionPriority | None = None
    source_field: str
    source_value: str
    normalized_value_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    eclass_property: EclassProperty | None = None
    property_area: ListInstanceBinding | None = None
    semantic_slot: SemanticSlotIdentity | None = None
    target: MappingTarget | None = None

    @model_validator(mode="after")
    def proposal_shape_matches_disposition(self) -> OpenPropertyProposal:
        proposed = self.disposition is OpenPropertyDisposition.PROPOSED
        fields = (
            self.eclass_property,
            self.property_area,
            self.semantic_slot,
            self.target,
        )
        if proposed and any(item is None for item in fields):
            raise ValueError("proposed open property requires ECLASS concept, slot, and target")
        if not proposed and any(item is not None for item in fields):
            raise ValueError("non-proposed disposition cannot carry a mapping target")
        return self


class OpenPropertyConflict(WireModel):
    kind: OpenPropertyConflictKind
    left_evidence_id: str
    right_evidence_id: str
    semantic_slot_key: str | None = None
    id_short: str | None = None
    reason: str


class OpenPropertyProposalReport(WireModel):
    """All evidence dispositions plus wildcard conflict diagnostics."""

    proposals: tuple[OpenPropertyProposal, ...]
    conflicts: tuple[OpenPropertyConflict, ...] = ()

    @model_validator(mode="after")
    def evidence_ids_are_unique(self) -> OpenPropertyProposalReport:
        identifiers = [item.evidence_id for item in self.proposals]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("open-property proposal evidence IDs must be unique")
        return self


def _context_key(context_path: tuple[str, ...]) -> str:
    return "context-" + sha256_json({"contextPath": context_path})[:24]


def _slot_identity(
    *,
    target: MappingTarget,
    semantic_id: str,
    context_path: tuple[str, ...],
) -> SemanticSlotIdentity:
    context_key = _context_key(context_path)
    payload = {
        "templateKey": target.template_key,
        "templateRelease": target.template_release,
        "templatePath": target.template_path,
        "semanticId": semantic_id,
        "contextKey": context_key,
    }
    return SemanticSlotIdentity(
        key="slot-" + sha256_json(payload)[:24],
        template_key=target.template_key,
        template_release=target.template_release,
        template_path=target.template_path,
        semantic_id=semantic_id,
        context_path=context_path,
        context_key=context_key,
    )


def _normalized_value_fingerprint(normalized: NormalizedEvidence) -> str:
    value = normalized.value
    if normalized.status.value == "unchanged":
        payload: object = {
            "kind": value.kind.value,
            "text": " ".join(value.raw.casefold().split()),
            "unit": value.unit.casefold() if value.unit else None,
        }
    else:
        payload = value.model_dump(
            mode="json",
            exclude={"raw"},
            by_alias=True,
        )
    return sha256_json(payload)


def _source_value(record: EvidenceRecord) -> str:
    value = str(record.value)
    return f"{value} {record.unit}" if record.unit else value


def _result_by_evidence(
    resolution: EclassResolutionReport,
) -> dict[str, EclassEvidenceResolution]:
    return {item.evidence_id: item for item in resolution.results}


def _diagnostics_by_evidence(
    diagnostics: EclassDiagnosticsReport,
) -> dict[str, EclassEvidenceDiagnostics]:
    return {item.evidence_id: item for item in diagnostics.evidence}


def _verified_property(
    *,
    consensus_choice: str,
    candidates: tuple[EclassProperty, ...],
) -> EclassProperty | None:
    return next((item for item in candidates if item.irdi == consensus_choice), None)


def _nonproposal_disposition(
    *,
    retrieval_status: EclassRetrievalStatus,
    consensus_choice: str | None,
    policy_priority: DecisionPriority | None,
) -> tuple[OpenPropertyDisposition, str]:
    if retrieval_status is EclassRetrievalStatus.NOT_APPLICABLE:
        return (
            OpenPropertyDisposition.NOT_APPLICABLE,
            "IDTA routing did not place this evidence in the open Technical Properties slot.",
        )
    if retrieval_status is EclassRetrievalStatus.RETRIEVAL_EMPTY:
        return (
            OpenPropertyDisposition.RETRIEVAL_EMPTY,
            "ECLASS retrieval returned no candidate properties.",
        )
    if retrieval_status in {
        EclassRetrievalStatus.NO_VERIFIED_CANDIDATES,
        EclassRetrievalStatus.PROVIDER_UNAVAILABLE,
    }:
        return (
            OpenPropertyDisposition.NO_VERIFIED_CANDIDATES,
            "No authoritative ECLASS candidate was available for bounded classification.",
        )
    if policy_priority is DecisionPriority.ALARM:
        return (
            OpenPropertyDisposition.CLASSIFICATION_ALARM,
            "ECLASS context scopes disagree strongly; no wildcard mapping was proposed.",
        )
    if consensus_choice == NO_ECLASS_MATCH:
        return (
            OpenPropertyDisposition.NO_ECLASS_MATCH,
            "Jev concluded that none of the verified ECLASS candidates matches the source fact.",
        )
    if consensus_choice in {None, UNRESOLVED_ECLASS}:
        return (
            OpenPropertyDisposition.UNRESOLVED,
            "ECLASS classification did not produce a stable verified concept.",
        )
    return (
        OpenPropertyDisposition.UNRESOLVED,
        "No verified ECLASS concept could be converted into an open-property proposal.",
    )


def build_open_property_proposals(
    *,
    package: ProductKnowledgePackage,
    normalization: NormalizationReport,
    eclass_resolution: EclassResolutionReport,
    eclass_diagnostics: EclassDiagnosticsReport,
    policy_settings: DecisionPolicySettings,
    templates: OfficialTemplateRepository,
) -> OpenPropertyProposalReport:
    """Create shadow wildcard mapping proposals from verified ECLASS consensus only."""

    records = {item.id: item for item in package.evidence}
    normalized = {item.evidence_id: item for item in normalization.evidence}
    results = _result_by_evidence(eclass_resolution)
    diagnostics = _diagnostics_by_evidence(eclass_diagnostics)
    if set(records) != set(normalized):
        raise ValueError("normalization must account for every evidence record")

    technical_data = templates.load("technical_data")
    proposals: list[OpenPropertyProposal] = []
    for record in package.evidence:
        result = results.get(record.id)
        diagnostic = diagnostics.get(record.id)
        if result is None or diagnostic is None:
            raise ValueError(f"missing ECLASS result or diagnostics for {record.id!r}")

        policy = classify_eclass_diagnostics(diagnostic, policy_settings)
        priority = policy.priority if policy is not None else None
        consensus_choice = diagnostic.consensus_choice
        property_ = (
            _verified_property(
                consensus_choice=consensus_choice,
                candidates=result.verified_candidates,
            )
            if consensus_choice is not None
            else None
        )
        if (
            result.retrieval_status is EclassRetrievalStatus.CANDIDATES_VERIFIED
            and property_ is not None
            and priority is not DecisionPriority.ALARM
        ):
            id_short = sanitize_id_short(
                property_.preferred_name,
                fallback="Property",
            )
            area_binding = technical_property_area_binding(record.context_path)
            target = mapping_target(
                technical_data,
                TECHNICAL_DATA_ARBITRARY_PROPERTY_PATH,
                id_short=id_short,
                semantic_id=property_.irdi,
                list_instance_bindings=(area_binding,),
            )
            slot = _slot_identity(
                target=target,
                semantic_id=property_.irdi,
                context_path=record.context_path,
            )
            proposals.append(
                OpenPropertyProposal(
                    evidence_id=record.id,
                    disposition=OpenPropertyDisposition.PROPOSED,
                    reason=(
                        "Verified ECLASS consensus produced a shadow Technical Data "
                        "ArbitraryProperty proposal."
                    ),
                    review_priority=priority,
                    source_field=record.source_label or record.predicate,
                    source_value=_source_value(record),
                    normalized_value_fingerprint=_normalized_value_fingerprint(
                        normalized[record.id]
                    ),
                    eclass_property=property_,
                    property_area=area_binding,
                    semantic_slot=slot,
                    target=target,
                )
            )
            continue

        disposition, reason = _nonproposal_disposition(
            retrieval_status=result.retrieval_status,
            consensus_choice=consensus_choice,
            policy_priority=priority,
        )
        proposals.append(
            OpenPropertyProposal(
                evidence_id=record.id,
                disposition=disposition,
                reason=reason,
                review_priority=priority,
                source_field=record.source_label or record.predicate,
                source_value=_source_value(record),
            )
        )

    conflicts = detect_open_property_conflicts(tuple(proposals))
    return OpenPropertyProposalReport(
        proposals=tuple(proposals),
        conflicts=conflicts,
    )


def detect_open_property_conflicts(
    proposals: tuple[OpenPropertyProposal, ...],
) -> tuple[OpenPropertyConflict, ...]:
    """Detect conflicts the current wildcard compiler cannot safely resolve."""

    mapped = tuple(
        item
        for item in proposals
        if item.disposition is OpenPropertyDisposition.PROPOSED
        and item.target is not None
        and item.semantic_slot is not None
    )

    conflicts: list[OpenPropertyConflict] = []
    seen: set[tuple[str, str, str]] = set()

    by_slot: dict[str, list[OpenPropertyProposal]] = defaultdict(list)
    for item in mapped:
        assert item.semantic_slot is not None
        by_slot[item.semantic_slot.key].append(item)
    for slot_key, items in by_slot.items():
        for index, left in enumerate(items):
            for right in items[index + 1 :]:
                if left.normalized_value_fingerprint == right.normalized_value_fingerprint:
                    identity = (
                        OpenPropertyConflictKind.REDUNDANT_DUPLICATE.value,
                        left.evidence_id,
                        right.evidence_id,
                    )
                    if identity in seen:
                        continue
                    seen.add(identity)
                    conflicts.append(
                        OpenPropertyConflict(
                            kind=OpenPropertyConflictKind.REDUNDANT_DUPLICATE,
                            left_evidence_id=left.evidence_id,
                            right_evidence_id=right.evidence_id,
                            semantic_slot_key=slot_key,
                            reason=(
                                "Equivalent evidence resolves to the same semantic slot and "
                                "wildcard instance path; promotion must deduplicate it before "
                                "compilation."
                            ),
                        )
                    )
                    continue
                identity = (
                    OpenPropertyConflictKind.VALUE_CONFLICT.value,
                    left.evidence_id,
                    right.evidence_id,
                )
                if identity in seen:
                    continue
                seen.add(identity)
                conflicts.append(
                    OpenPropertyConflict(
                        kind=OpenPropertyConflictKind.VALUE_CONFLICT,
                        left_evidence_id=left.evidence_id,
                        right_evidence_id=right.evidence_id,
                        semantic_slot_key=slot_key,
                        reason=(
                            "Different normalized values compete for the same "
                            "context-aware semantic slot."
                        ),
                    )
                )

    by_projection: dict[tuple[object, ...], list[OpenPropertyProposal]] = defaultdict(list)
    for item in mapped:
        assert item.target is not None
        by_projection[item.target.projection_identity].append(item)
    for _, items in by_projection.items():
        distinct_semantic_ids = {
            item.semantic_slot.semantic_id for item in items if item.semantic_slot is not None
        }
        if len(distinct_semantic_ids) <= 1:
            continue
        for index, left in enumerate(items):
            for right in items[index + 1 :]:
                assert left.semantic_slot is not None
                assert right.semantic_slot is not None
                if left.semantic_slot.semantic_id == right.semantic_slot.semantic_id:
                    continue
                identity = (
                    OpenPropertyConflictKind.ID_SHORT_COLLISION.value,
                    left.evidence_id,
                    right.evidence_id,
                )
                if identity in seen:
                    continue
                seen.add(identity)
                conflicts.append(
                    OpenPropertyConflict(
                        kind=OpenPropertyConflictKind.ID_SHORT_COLLISION,
                        left_evidence_id=left.evidence_id,
                        right_evidence_id=right.evidence_id,
                        id_short=(
                            items[0].target.id_short if items[0].target is not None else None
                        ),
                        reason=(
                            "Different semantic slots sanitize to the same wildcard "
                            "instance path/idShort."
                        ),
                    )
                )

    by_semantic_id: dict[str, list[OpenPropertyProposal]] = defaultdict(list)
    for item in mapped:
        assert item.semantic_slot is not None
        by_semantic_id[item.semantic_slot.semantic_id].append(item)
    for _, items in by_semantic_id.items():
        contexts = {
            item.semantic_slot.context_key for item in items if item.semantic_slot is not None
        }
        if len(contexts) <= 1:
            continue
        projection_identities = {
            item.target.projection_identity for item in items if item.target is not None
        }
        if len(projection_identities) != 1:
            continue
        for index, left in enumerate(items):
            for right in items[index + 1 :]:
                assert left.semantic_slot is not None
                assert right.semantic_slot is not None
                if left.semantic_slot.context_key == right.semantic_slot.context_key:
                    continue
                identity = (
                    OpenPropertyConflictKind.PROJECTION_CONTEXT_COLLISION.value,
                    left.evidence_id,
                    right.evidence_id,
                )
                if identity in seen:
                    continue
                seen.add(identity)
                conflicts.append(
                    OpenPropertyConflict(
                        kind=OpenPropertyConflictKind.PROJECTION_CONTEXT_COLLISION,
                        left_evidence_id=left.evidence_id,
                        right_evidence_id=right.evidence_id,
                        id_short=left.target.id_short if left.target is not None else None,
                        reason=(
                            "The same semantic concept occurs in different source contexts, "
                            "but the current compiler would project both to one wildcard path. "
                            "Component/list-instance identity must be resolved first."
                        ),
                    )
                )

    return tuple(conflicts)

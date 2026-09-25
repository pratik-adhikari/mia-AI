"""Detect requirement-level conflicts without invalidating unrelated human work."""

from __future__ import annotations

from pydantic import Field

from mia_dpp.domain.base import WireModel
from mia_dpp.domain.evidence import EvidenceRecord, EvidenceStatus, ProductKnowledgePackage
from mia_dpp.domain.mappings import (
    FieldMapping,
    MappingAssessment,
    MappingResult,
    MappingStatus,
)
from mia_dpp.domain.targets import TemplateIndex


class EvidenceConflict(WireModel):
    """Two values that compete for the same official requirement."""

    requirement_id: str = Field(min_length=1)
    existing_evidence_id: str = Field(min_length=1)
    incoming_evidence_id: str = Field(min_length=1)
    existing_value: str
    incoming_value: str
    previously_reviewed_by: str | None = None


def detect_review_conflicts(
    existing_package: ProductKnowledgePackage,
    incoming_package: ProductKnowledgePackage,
    existing_mapping: MappingResult,
    incoming_mapping: MappingResult,
    index: TemplateIndex,
) -> tuple[EvidenceConflict, ...]:
    """Find only new values that contradict a previously human-reviewed requirement mapping."""

    existing_evidence = {item.id: item for item in existing_package.evidence}
    incoming_evidence = {item.id: item for item in incoming_package.evidence}
    existing_by_requirement: dict[str, list[FieldMapping]] = {}
    for mapping in existing_mapping.mapped:
        if not mapping.human_reviewed:
            continue
        requirement_id = requirement_id_for_mapping(mapping, index)
        if requirement_id is not None:
            existing_by_requirement.setdefault(requirement_id, []).append(mapping)

    conflicts: list[EvidenceConflict] = []
    seen: set[tuple[str, str, str]] = set()
    for incoming in (*incoming_mapping.mapped, *incoming_mapping.ambiguous):
        requirement_id = requirement_id_for_mapping(incoming, index)
        if requirement_id is None:
            continue
        new_record = incoming_evidence.get(incoming.evidence_id)
        if new_record is None:
            continue
        for previous in existing_by_requirement.get(requirement_id, ()):
            old_record = existing_evidence.get(previous.evidence_id)
            if old_record is None:
                continue
            if _value(old_record) == _value(new_record):
                continue
            identity = (requirement_id, previous.evidence_id, incoming.evidence_id)
            if identity in seen:
                continue
            seen.add(identity)
            conflicts.append(
                EvidenceConflict(
                    requirement_id=requirement_id,
                    existing_evidence_id=previous.evidence_id,
                    incoming_evidence_id=incoming.evidence_id,
                    existing_value=_display(old_record),
                    incoming_value=_display(new_record),
                    previously_reviewed_by=previous.human_actor_name,
                )
            )
    return tuple(conflicts)


def mark_conflicting_evidence(
    package: ProductKnowledgePackage,
    conflicts: tuple[EvidenceConflict, ...],
) -> ProductKnowledgePackage:
    conflicted = {
        evidence_id
        for item in conflicts
        for evidence_id in (item.existing_evidence_id, item.incoming_evidence_id)
    }
    if not conflicted:
        return package
    return package.model_copy(
        update={
            "evidence": tuple(
                item.model_copy(update={"status": EvidenceStatus.CONFLICTING})
                if item.id in conflicted and item.status is not EvidenceStatus.REJECTED
                else item
                for item in package.evidence
            )
        }
    )


def require_review_for_conflicts(
    mapping: MappingResult,
    index: TemplateIndex,
    conflicts: tuple[EvidenceConflict, ...],
) -> MappingResult:
    requirement_ids = {item.requirement_id for item in conflicts}
    if not requirement_ids:
        return mapping

    def demote(item: FieldMapping) -> FieldMapping:
        requirement_id = requirement_id_for_mapping(item, index)
        if requirement_id not in requirement_ids:
            return item
        return item.model_copy(
            update={
                "status": MappingStatus.REVIEW,
                "assessment": MappingAssessment(
                    basis=item.assessment.basis,
                    review_required=True,
                    reason=(
                        "New durable evidence supplies a different value for this reviewed "
                        "requirement."
                    ),
                    uncertainties=tuple(
                        dict.fromkeys(
                            (
                                *item.assessment.uncertainties,
                                "A competing value was discovered after the previous human "
                                "decision.",
                            )
                        )
                    ),
                ),
            }
        )

    return mapping.model_copy(
        update={
            "mapped": tuple(demote(item) for item in mapping.mapped),
            "ambiguous": tuple(demote(item) for item in mapping.ambiguous),
        }
    )


def requirement_id_for_mapping(mapping: FieldMapping, index: TemplateIndex) -> str | None:
    requirement = next(
        (
            item
            for item in index.requirements
            if item.template_key == mapping.target.template_key
            and item.template_release == mapping.target.template_release
            and item.template_path == mapping.target.template_path
        ),
        None,
    )
    return requirement.id if requirement is not None else None


def _value(record: EvidenceRecord) -> tuple[str, str]:
    return (" ".join(str(record.value).casefold().split()), (record.unit or "").casefold())


def _display(record: EvidenceRecord) -> str:
    text = str(record.value)
    return f"{text} {record.unit}" if record.unit else text

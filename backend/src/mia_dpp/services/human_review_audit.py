"""Translate mutable review submissions into immutable human audit records."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from mia_dpp.domain.mappings import SemanticReviewItem
from mia_dpp.domain.product_work import HumanReviewAction, HumanReviewRecord
from mia_dpp.services.human_submission import MappingReviewDecision


def mapping_review_records(
    *,
    user_id: str,
    product_id: str,
    run_id: str,
    thread_id: str,
    mapping_cycle_id: str | None,
    actor_name: str | None,
    decision: MappingReviewDecision,
    before: SemanticReviewItem,
    after: SemanticReviewItem,
    proposed_value: str | None,
    final_value: str | None,
) -> tuple[HumanReviewRecord, ...]:
    """Record every material human action without collapsing target and value corrections."""

    common = {
        "user_id": user_id,
        "product_id": product_id,
        "run_id": run_id,
        "thread_id": thread_id,
        "mapping_cycle_id": mapping_cycle_id,
        "review_id": decision.review_id,
        "evidence_id": before.evidence_id,
        "proposed_mapping_id": before.mapping.id if before.mapping else None,
        "proposed_requirement_id": before.requirement_id,
        "final_mapping_id": after.mapping.id if after.mapping else None,
        "final_requirement_id": after.requirement_id,
        "corrected_evidence_id": (
            after.evidence_id if after.evidence_id != before.evidence_id else None
        ),
        "proposed_value": proposed_value,
        "final_value": final_value,
        "proposed_target_path": (
            before.mapping.target.template_path if before.mapping is not None else ()
        ),
        "final_target_path": (
            after.mapping.target.template_path if after.mapping is not None else ()
        ),
        "proposed_semantic_id": (
            before.mapping.target.semantic_id.primary_value if before.mapping is not None else None
        ),
        "final_semantic_id": (
            after.mapping.target.semantic_id.primary_value if after.mapping is not None else None
        ),
        "proposed_list_instance_bindings": (
            before.mapping.target.list_instance_bindings if before.mapping is not None else ()
        ),
        "final_list_instance_bindings": (
            after.mapping.target.list_instance_bindings if after.mapping is not None else ()
        ),
        "actor_name": actor_name,
        "comment": decision.comment,
    }
    records: list[HumanReviewRecord] = []

    normalized = {"approve": "keep", "correct": "change_target"}.get(
        decision.decision,
        decision.decision,
    )
    if normalized == "reject":
        records.append(_record(HumanReviewAction.REJECTED_EVIDENCE, common))
        return tuple(records)
    if normalized == "irrelevant":
        records.append(_record(HumanReviewAction.MARKED_IRRELEVANT, common))
        return tuple(records)
    if normalized == "unmapped":
        records.append(_record(HumanReviewAction.MARKED_UNMAPPED, common))
        return tuple(records)

    target_changed = (
        before.requirement_id != after.requirement_id
        or (
            before.mapping is not None
            and after.mapping is not None
            and before.mapping.target != after.mapping.target
        )
        or (before.mapping is None) != (after.mapping is None)
    )
    value_changed = before.evidence_id != after.evidence_id
    if target_changed:
        records.append(_record(HumanReviewAction.CORRECTED_TARGET, common))
    if value_changed:
        records.append(
            _record(
                HumanReviewAction.CORRECTED_VALUE,
                {**common, "value_kind": "verified"},
            )
        )
    if not records:
        action = (
            HumanReviewAction.RECONFIRMED_MAPPING
            if before.mapping is not None and before.mapping.human_reviewed
            else HumanReviewAction.ACCEPTED_MAPPING
        )
        records.append(_record(action, common))
    return tuple(records)


def supplied_value_record(
    *,
    user_id: str,
    product_id: str,
    run_id: str,
    thread_id: str,
    requirement_id: str,
    evidence_id: str,
    mapping_id: str,
    actor_name: str | None,
    use_dummy: bool,
    final_value: str,
    final_target_path: tuple[str, ...],
) -> HumanReviewRecord:
    return HumanReviewRecord(
        id=f"human-review-{uuid.uuid4().hex}",
        user_id=user_id,
        product_id=product_id,
        run_id=run_id,
        thread_id=thread_id,
        evidence_id=evidence_id,
        final_mapping_id=mapping_id,
        final_requirement_id=requirement_id,
        final_value=final_value,
        final_target_path=final_target_path,
        action=(
            HumanReviewAction.SUPPLIED_DUMMY if use_dummy else HumanReviewAction.SUPPLIED_VALUE
        ),
        actor_name=actor_name,
        value_kind="dummy" if use_dummy else "verified",
    )


def _record(
    action: HumanReviewAction,
    values: Mapping[str, object],
) -> HumanReviewRecord:
    return HumanReviewRecord(
        id=f"human-review-{uuid.uuid4().hex}",
        action=action,
        **values,
    )

"""Architecture-neutral helpers for merging incremental mapping results."""

from __future__ import annotations

from typing import Any

from mia_dpp.domain.mappings import MappingResult


def merge_mapping_results(existing: MappingResult, incoming: MappingResult) -> MappingResult:
    """Append outcomes for new evidence while preserving every existing decision."""

    known = (
        {item.evidence_id for item in (*existing.mapped, *existing.ambiguous, *existing.rejected)}
        | set(existing.unmatched_evidence_ids)
        | set(existing.irrelevant_evidence_ids)
        | set(existing.rejected_evidence_ids)
    )

    def new_mappings(items: tuple[Any, ...]) -> tuple[Any, ...]:
        return tuple(item for item in items if item.evidence_id not in known)

    def new_ids(items: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(item for item in items if item not in known)

    return MappingResult(
        mapped=(*existing.mapped, *new_mappings(incoming.mapped)),
        ambiguous=(*existing.ambiguous, *new_mappings(incoming.ambiguous)),
        rejected=(*existing.rejected, *new_mappings(incoming.rejected)),
        unmatched_evidence_ids=(
            *existing.unmatched_evidence_ids,
            *new_ids(incoming.unmatched_evidence_ids),
        ),
        irrelevant_evidence_ids=(
            *existing.irrelevant_evidence_ids,
            *new_ids(incoming.irrelevant_evidence_ids),
        ),
        rejected_evidence_ids=(
            *existing.rejected_evidence_ids,
            *new_ids(incoming.rejected_evidence_ids),
        ),
        outcomes=(
            *existing.outcomes,
            *(item for item in incoming.outcomes if item.evidence_id not in known),
        ),
    )

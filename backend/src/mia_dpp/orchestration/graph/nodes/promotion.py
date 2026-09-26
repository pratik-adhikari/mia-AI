"""Thin LangGraph adapter for semantic mapping promotion."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

from mia_dpp.runtime.run_context import RunContext
from mia_dpp.runtime.services import ServiceContainer
from mia_dpp.services.semantic_promotion import SemanticPromotionService
from mia_dpp.orchestration.graph.state import MiaWorkflowState


async def promote_semantic_mapping(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    if not runtime.context.semantic_promotion_enabled:
        return {}

    result = SemanticPromotionService(
        catalogue=runtime.context.catalogue,
        artifacts=runtime.context.artifacts,
        templates=runtime.context.templates,
        mapping_review=runtime.context.mapping_review,
    ).promote(
        RunContext.from_mapping(state),
        evidence_artifact_id=state["evidence_artifact_id"],
        semantic_mapping_artifact_id=state["semantic_mapping_artifact_id"],
        review_items_artifact_id=state["review_items_artifact_id"],
        open_property_proposals_artifact_id=state["open_property_proposals_artifact_id"],
        eclass_resolution_artifact_id=state["eclass_resolution_artifact_id"],
        eclass_diagnostics_artifact_id=state["eclass_diagnostics_artifact_id"],
        targets_artifact_id=state["targets_artifact_id"],
        mapping_cycle_id=state.get("mapping_cycle_id") or None,
        expected_snapshot_version=int(state.get("product_snapshot_version", 0)),
        source_generation=int(state.get("source_generation", 0)),
    )
    return {
        "semantic_mapping_artifact_id": result.mapping_artifact_id,
        "semantic_promotion_artifact_id": result.promotion_artifact_id,
        "review_items_artifact_id": result.review_items_artifact_id,
        "mapping_cycle_id": result.mapping_cycle_id,
        "review_required": result.review_required,
        "reviewed_mapping_artifact_id": "",
        "review_fingerprint": "",
        "product_snapshot_version": result.product_snapshot_version,
    }

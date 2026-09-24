"""Promote verified semantic-engine results into the authoritative mapping workflow."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.mappings import MappingResult, SemanticReviewItem
from mia_dpp.domain.product_work import ProductWorkStage
from mia_dpp.domain.targets import TemplateIndex
from mia_dpp.semantic.eclass_diagnostics import EclassDiagnosticsReport
from mia_dpp.semantic.eclass_resolution import EclassResolutionReport
from mia_dpp.semantic.open_property import OpenPropertyProposalReport
from mia_dpp.semantic.promotion import promote_open_properties
from mia_dpp.workflow.context import MiaContext
from mia_dpp.workflow.product_snapshot import update_product_snapshot
from mia_dpp.workflow.state import MiaWorkflowState
from mia_dpp.workflow.workspace import RunWorkspace


async def promote_semantic_mapping(
    state: MiaWorkflowState,
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    """Merge verified open-property semantics before review/coverage routing."""

    if not runtime.context.semantic_promotion_enabled:
        return {}

    work = RunWorkspace(state, runtime.context)
    package = work.load_state("evidence_artifact_id", ProductKnowledgePackage)
    mapping = work.load_state("semantic_mapping_artifact_id", MappingResult)
    existing_reviews = tuple(
        SemanticReviewItem.model_validate(item)
        for item in work.load_json("review_items_artifact_id")
    )
    proposals = work.load_state(
        "open_property_proposals_artifact_id",
        OpenPropertyProposalReport,
    )
    resolution = work.load_state(
        "eclass_resolution_artifact_id",
        EclassResolutionReport,
    )
    diagnostics = work.load_state(
        "eclass_diagnostics_artifact_id",
        EclassDiagnosticsReport,
    )

    index = work.load_state("targets_artifact_id", TemplateIndex)
    cycle_seed = state.get("mapping_cycle_id") or work.ctx.mapping_review.cycle_id(
        package,
        index,
        mapping,
    )
    promoted = promote_open_properties(
        package=package,
        mapping=mapping,
        existing_review_items=existing_reviews,
        proposals=proposals,
        eclass_resolution=resolution,
        eclass_diagnostics=diagnostics,
        templates=work.ctx.templates,
        cycle_seed=cycle_seed,
    )
    work.ctx.mapping_review.validate_complete_accounting(package, promoted.mapping)

    cycle_id = work.ctx.mapping_review.cycle_id(
        package,
        index,
        promoted.mapping,
    )
    promotion_id = work.put_model(
        "mapping/semantic-promotion.json",
        promoted.report,
        derived_from=(
            work.state_id("semantic_mapping_artifact_id"),
            work.state_id("open_property_proposals_artifact_id"),
            work.state_id("eclass_diagnostics_artifact_id"),
        ),
    )
    mapping_id = work.put_model(
        "mapping/promoted.json",
        promoted.mapping,
        derived_from=(
            work.state_id("semantic_mapping_artifact_id"),
            promotion_id,
        ),
    )
    review_id = work.put_json(
        "mapping/review-items-promoted.json",
        [
            item.model_dump(mode="json", by_alias=True)
            for item in promoted.review_items
        ],
        derived_from=(mapping_id, promotion_id),
    )
    review_required = bool(promoted.review_items)

    work.event(
        "mapping.semantic_promotion_completed",
        (
            f"Promoted {len(promoted.report.promoted_evidence_ids)} verified open "
            f"Technical Properties; {len(promoted.review_items)} items require review."
        ),
        metadata={
            "promoted": len(promoted.report.promoted_evidence_ids),
            "review": len(promoted.review_items),
            "unmapped": len(promoted.report.unmapped_evidence_ids),
            "conflicts": len(promoted.report.conflict_evidence_ids),
            "promotionArtifactId": promotion_id,
        },
    )
    snapshot = update_product_snapshot(
        work,
        ProductWorkStage.HUMAN_REVIEW if review_required else ProductWorkStage.MAPPING,
        semantic_mapping_artifact_id=mapping_id,
        reviewed_mapping_artifact_id=None,
        semantic_promotion_artifact_id=promotion_id,
        mapping_cycle_id=cycle_id,
        human_review_pending=review_required,
        review_fingerprint=None,
    )
    return {
        "semantic_mapping_artifact_id": mapping_id,
        "semantic_promotion_artifact_id": promotion_id,
        "review_items_artifact_id": review_id,
        "mapping_cycle_id": cycle_id,
        "review_required": review_required,
        "reviewed_mapping_artifact_id": "",
        "review_fingerprint": "",
        "product_snapshot_version": snapshot.version,
    }

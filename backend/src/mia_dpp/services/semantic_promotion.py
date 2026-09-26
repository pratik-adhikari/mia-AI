"""Promote verified semantic-engine results into authoritative mappings."""

from __future__ import annotations

from dataclasses import dataclass

from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.mappings import MappingResult, SemanticReviewItem
from mia_dpp.domain.product_work import ProductWorkStage
from mia_dpp.domain.targets import TemplateIndex
from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.runtime.run_context import RunContext
from mia_dpp.runtime.run_store import RunStore
from mia_dpp.semantic.eclass_diagnostics import EclassDiagnosticsReport
from mia_dpp.semantic.eclass_resolution import EclassResolutionReport
from mia_dpp.semantic.open_property import OpenPropertyProposalReport
from mia_dpp.semantic.promotion import promote_open_properties
from mia_dpp.services.product_snapshot import update_product_snapshot
from mia_dpp.storage.base import ArtifactStore
from mia_dpp.tools.mapping.review import MappingReviewService


@dataclass(frozen=True, slots=True)
class SemanticPromotionResult:
    mapping_artifact_id: str
    promotion_artifact_id: str
    review_items_artifact_id: str
    mapping_cycle_id: str
    review_required: bool
    product_snapshot_version: int


class SemanticPromotionService:
    """Promote verified open-property semantics without orchestration dependencies."""

    def __init__(
        self,
        *,
        catalogue: ProductCatalogue,
        artifacts: ArtifactStore,
        templates: OfficialTemplateRepository,
        mapping_review: MappingReviewService,
    ) -> None:
        self._catalogue = catalogue
        self._artifacts = artifacts
        self._templates = templates
        self._mapping_review = mapping_review

    def promote(
        self,
        context: RunContext,
        *,
        evidence_artifact_id: str,
        semantic_mapping_artifact_id: str,
        review_items_artifact_id: str,
        open_property_proposals_artifact_id: str,
        eclass_resolution_artifact_id: str,
        eclass_diagnostics_artifact_id: str,
        targets_artifact_id: str,
        mapping_cycle_id: str | None,
        expected_snapshot_version: int,
        source_generation: int,
    ) -> SemanticPromotionResult:
        work = RunStore(context, self._catalogue, self._artifacts)
        package = work.load(evidence_artifact_id, ProductKnowledgePackage)
        mapping = work.load(semantic_mapping_artifact_id, MappingResult)
        existing_reviews = tuple(
            SemanticReviewItem.model_validate(item)
            for item in work.load_json(review_items_artifact_id)
        )
        proposals = work.load(
            open_property_proposals_artifact_id,
            OpenPropertyProposalReport,
        )
        resolution = work.load(
            eclass_resolution_artifact_id,
            EclassResolutionReport,
        )
        diagnostics = work.load(
            eclass_diagnostics_artifact_id,
            EclassDiagnosticsReport,
        )
        index = work.load(targets_artifact_id, TemplateIndex)
        cycle_seed = mapping_cycle_id or self._mapping_review.cycle_id(
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
            templates=self._templates,
            cycle_seed=cycle_seed,
        )
        self._mapping_review.validate_complete_accounting(package, promoted.mapping)

        cycle_id = self._mapping_review.cycle_id(
            package,
            index,
            promoted.mapping,
        )
        promotion_id = work.put_model(
            "mapping/semantic-promotion.json",
            promoted.report,
            derived_from=(
                semantic_mapping_artifact_id,
                open_property_proposals_artifact_id,
                eclass_diagnostics_artifact_id,
            ),
        )
        mapping_id = work.put_model(
            "mapping/promoted.json",
            promoted.mapping,
            derived_from=(semantic_mapping_artifact_id, promotion_id),
        )
        review_id = work.put_json(
            "mapping/review-items-promoted.json",
            [item.model_dump(mode="json", by_alias=True) for item in promoted.review_items],
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
            self._catalogue,
            context,
            ProductWorkStage.HUMAN_REVIEW if review_required else ProductWorkStage.MAPPING,
            expected_version=expected_snapshot_version,
            source_generation=source_generation,
            semantic_mapping_artifact_id=mapping_id,
            reviewed_mapping_artifact_id=None,
            semantic_promotion_artifact_id=promotion_id,
            mapping_cycle_id=cycle_id,
            human_review_pending=review_required,
            review_fingerprint=None,
        )
        return SemanticPromotionResult(
            mapping_artifact_id=mapping_id,
            promotion_artifact_id=promotion_id,
            review_items_artifact_id=review_id,
            mapping_cycle_id=cycle_id,
            review_required=review_required,
            product_snapshot_version=snapshot.version,
        )

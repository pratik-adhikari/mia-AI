"""Decide whether a product request should reuse durable work or reacquire sources."""

from __future__ import annotations

from mia_dpp.domain.product_work import ProductWorkStage, ReuseDecision, ReuseMode
from mia_dpp.persistence.catalogue import ProductCatalogue


class ProductReuseService:
    """Keep reuse policy outside LangGraph nodes and semantic mapper implementations."""

    def __init__(self, catalogue: ProductCatalogue) -> None:
        self._catalogue = catalogue

    def decide(
        self,
        product_id: str,
        *,
        user_id: str,
        refresh_requested: bool,
    ) -> ReuseDecision:
        if refresh_requested:
            return ReuseDecision(
                mode=ReuseMode.REFRESH_SOURCES,
                reason="The user explicitly requested fresh source acquisition.",
            )

        snapshot = self._catalogue.get_product_work_snapshot(product_id, user_id=user_id)
        pending_research = self._catalogue.latest_completed_background_job(
            product_id,
            user_id=user_id,
        )
        research_unintegrated = (
            pending_research is not None
            and (
                snapshot is None
                or snapshot.last_integrated_research_job_id != pending_research.id
            )
        )
        if research_unintegrated:
            evidence_id = (
                snapshot.evidence_artifact_id
                if snapshot is not None and snapshot.evidence_artifact_id
                else pending_research.metadata.get("seedEvidenceArtifactId")
            )
            if isinstance(evidence_id, str):
                return ReuseDecision(
                    mode=ReuseMode.CONTINUE_SAVED_WORK,
                    reason="Completed background research is newer than the integrated product state.",
                    seeded_from_run_id=(
                        snapshot.run_id if snapshot is not None else pending_research.run_id
                    ),
                    evidence_artifact_id=evidence_id,
                    reviewed_mapping_artifact_id=(
                        snapshot.reviewed_mapping_artifact_id if snapshot is not None else None
                    ),
                    pending_research_job_id=pending_research.id,
                )

        completed = self._catalogue.latest_successful_dpp(product_id, user_id=user_id)
        if snapshot is not None and snapshot.evidence_artifact_id:
            snapshot_matches_dpp = (
                completed is not None
                and snapshot.workflow_stage is ProductWorkStage.COMPLETED
                and snapshot.run_id == completed.run_id
            )
            if not snapshot_matches_dpp:
                return ReuseDecision(
                    mode=ReuseMode.CONTINUE_SAVED_WORK,
                    reason="Saved product work is newer or incomplete relative to the latest DPP.",
                    seeded_from_run_id=snapshot.run_id,
                    evidence_artifact_id=snapshot.evidence_artifact_id,
                    reviewed_mapping_artifact_id=snapshot.reviewed_mapping_artifact_id,
                )

        if completed is not None:
            return ReuseDecision(
                mode=ReuseMode.REUSE_COMPLETED_DPP,
                reason="The latest deployable DPP still matches the authoritative product state.",
                seeded_from_run_id=completed.run_id,
                reused_dpp_version_id=completed.id,
            )

        if snapshot is not None and snapshot.evidence_artifact_id:
            return ReuseDecision(
                mode=ReuseMode.CONTINUE_SAVED_WORK,
                reason="The product has an authoritative durable work snapshot.",
                seeded_from_run_id=snapshot.run_id,
                evidence_artifact_id=snapshot.evidence_artifact_id,
                reviewed_mapping_artifact_id=snapshot.reviewed_mapping_artifact_id,
            )

        prior_run, reusable = self._catalogue.latest_reusable_artifacts(
            product_id,
            user_id=user_id,
        )
        if prior_run is not None and "evidence" in reusable:
            return ReuseDecision(
                mode=ReuseMode.CONTINUE_SAVED_WORK,
                reason="Reusable historical artifacts were found and will bootstrap a snapshot.",
                seeded_from_run_id=prior_run.id,
                evidence_artifact_id=reusable["evidence"].id,
                reviewed_mapping_artifact_id=(
                    reusable["reviewed_mapping"].id if "reviewed_mapping" in reusable else None
                ),
            )

        return ReuseDecision(
            mode=ReuseMode.FRESH,
            reason="No reusable durable work exists for this product.",
        )

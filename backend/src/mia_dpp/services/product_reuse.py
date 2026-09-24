"""Decide whether a product request should reuse durable work or reacquire sources."""

from __future__ import annotations

from mia_dpp.domain.product_work import ReuseDecision, ReuseMode
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

        completed = self._catalogue.latest_successful_dpp(product_id, user_id=user_id)
        if completed is not None:
            return ReuseDecision(
                mode=ReuseMode.REUSE_COMPLETED_DPP,
                reason="A deployable DPP already exists for this product.",
                seeded_from_run_id=completed.run_id,
                reused_dpp_version_id=completed.id,
            )

        snapshot = self._catalogue.get_product_work_snapshot(product_id, user_id=user_id)
        if snapshot is not None and snapshot.evidence_artifact_id:
            return ReuseDecision(
                mode=ReuseMode.CONTINUE_SAVED_WORK,
                reason="The product has an authoritative durable work snapshot.",
                seeded_from_run_id=snapshot.run_id,
                evidence_artifact_id=snapshot.evidence_artifact_id,
                reviewed_mapping_artifact_id=snapshot.reviewed_mapping_artifact_id,
            )

        # One-time compatibility path for products created before product snapshots existed.
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

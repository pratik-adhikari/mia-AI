"""Reusable product identity, run creation, and completed-DPP reuse operations."""

from __future__ import annotations

from dataclasses import dataclass

from mia_dpp.domain.product import RunStatus
from mia_dpp.domain.product_work import ReuseMode
from mia_dpp.persistence.catalogue import ActiveProductRunExists, ProductCatalogue
from mia_dpp.runtime.run_context import RunContext
from mia_dpp.services.product_reuse import ProductReuseService
from mia_dpp.storage.base import ArtifactStore


@dataclass(frozen=True, slots=True)
class ProductResolutionRequest:
    product_url: str
    user_id: str
    thread_id: str
    refresh_requested: bool = False
    max_research_attempts: int = 2


@dataclass(frozen=True, slots=True)
class ProductResolutionResult:
    product_id: str
    run_id: str
    reuse_mode: ReuseMode
    reuse_prior_work: bool
    seeded_from_run_id: str | None
    evidence_artifact_id: str | None
    reviewed_mapping_artifact_id: str | None
    background_job_id: str | None
    reused_dpp_version_id: str | None
    product_name: str | None
    manufacturer: str | None
    image_url: str | None
    product_snapshot_version: int
    workflow_generation: int
    source_generation: int
    status: str
    cache_hit: bool = False
    max_research_attempts: int | None = None


@dataclass(frozen=True, slots=True)
class ReusedDppResult:
    dpp_artifact_id: str
    aas_artifact_id: str | None
    validation_artifact_id: str | None
    source_fingerprint: str | None
    dpp_version_id: str
    status: str = "reused"


class ProductLifecycleService:
    """Resolve durable product/run identity independently of orchestration."""

    def __init__(self, catalogue: ProductCatalogue, artifacts: ArtifactStore) -> None:
        self._catalogue = catalogue
        self._reuse = ProductReuseService(catalogue, artifacts)

    def resolve(self, request: ProductResolutionRequest) -> ProductResolutionResult:
        product, _ = self._catalogue.get_or_create_product(
            request.product_url,
            user_id=request.user_id,
        )
        active = self._catalogue.latest_active_run(product.id, user_id=request.user_id)
        if active is not None:
            if active.thread_id != request.thread_id:
                raise ActiveProductRunExists(active)
            snapshot = self._catalogue.get_product_work_snapshot(
                product.id,
                user_id=request.user_id,
            )
            return ProductResolutionResult(
                product_id=product.id,
                run_id=active.id,
                reuse_mode=ReuseMode.RESUME_CHECKPOINT,
                reuse_prior_work=True,
                seeded_from_run_id=active.id,
                evidence_artifact_id=(
                    snapshot.evidence_artifact_id
                    if snapshot and snapshot.evidence_artifact_id
                    else None
                ),
                reviewed_mapping_artifact_id=(
                    snapshot.reviewed_mapping_artifact_id
                    if snapshot and snapshot.reviewed_mapping_artifact_id
                    else None
                ),
                background_job_id=None,
                reused_dpp_version_id=None,
                product_name=product.name,
                manufacturer=product.manufacturer,
                image_url=product.image_url,
                product_snapshot_version=snapshot.version if snapshot is not None else 0,
                workflow_generation=active.workflow_generation,
                source_generation=snapshot.source_generation if snapshot is not None else 0,
                status=(
                    "awaiting_human"
                    if active.status is RunStatus.AWAITING_HUMAN
                    else "running"
                ),
            )

        durable_snapshot = self._catalogue.get_product_work_snapshot(
            product.id,
            user_id=request.user_id,
        )
        decision = self._reuse.decide(
            product.id,
            user_id=request.user_id,
            refresh_requested=request.refresh_requested,
        )
        base_source_generation = (
            durable_snapshot.source_generation if durable_snapshot is not None else 0
        )
        source_generation = (
            base_source_generation + 1
            if decision.mode in {ReuseMode.FRESH, ReuseMode.REFRESH_SOURCES}
            else base_source_generation
        )
        if source_generation == 0:
            source_generation = 1

        cached = (
            self._catalogue.latest_successful_dpp(product.id, user_id=request.user_id)
            if decision.mode is ReuseMode.REUSE_COMPLETED_DPP
            else None
        )
        run = self._catalogue.start_run(
            product.id,
            request.thread_id,
            user_id=request.user_id,
            refresh_requested=request.refresh_requested,
            reused_from_run_id=cached.run_id if cached else None,
            seeded_from_run_id=decision.seeded_from_run_id,
        )
        self._catalogue.add_event(
            run.id,
            "product.resolved",
            "Resolved product identity and checked the durable DPP cache.",
            metadata={
                "cacheHit": cached is not None,
                "reuseMode": decision.mode.value,
                "reusedPriorWork": decision.mode is ReuseMode.CONTINUE_SAVED_WORK,
                "seededFromRunId": decision.seeded_from_run_id,
                "canonicalUrl": product.canonical_url,
            },
        )
        return ProductResolutionResult(
            product_id=product.id,
            run_id=run.id,
            cache_hit=cached is not None,
            reuse_mode=decision.mode,
            reuse_prior_work=decision.mode is ReuseMode.CONTINUE_SAVED_WORK,
            seeded_from_run_id=decision.seeded_from_run_id,
            evidence_artifact_id=decision.evidence_artifact_id,
            reviewed_mapping_artifact_id=decision.reviewed_mapping_artifact_id,
            background_job_id=decision.pending_research_job_id,
            reused_dpp_version_id=decision.reused_dpp_version_id,
            product_name=product.name,
            manufacturer=product.manufacturer,
            image_url=product.image_url,
            product_snapshot_version=(
                durable_snapshot.version if durable_snapshot is not None else 0
            ),
            workflow_generation=run.workflow_generation,
            source_generation=source_generation,
            status="reused" if cached else "running",
            max_research_attempts=request.max_research_attempts,
        )

    def reuse_completed_dpp(self, context: RunContext) -> ReusedDppResult:
        version = self._catalogue.latest_successful_dpp(
            context.product_id,
            user_id=context.user_id,
        )
        if version is None:
            raise RuntimeError("cache route selected without a successful DPP")
        self._catalogue.finish_run(context.run_id, RunStatus.REUSED)
        self._catalogue.add_event(
            context.run_id,
            "dpp.reused",
            "Returned the existing successful DPP without repeating extraction or mapping.",
            metadata={"dppVersionId": version.id, "version": version.version},
        )
        return ReusedDppResult(
            dpp_artifact_id=version.dpp_artifact_id,
            aas_artifact_id=version.aas_artifact_id,
            validation_artifact_id=version.validation_artifact_id,
            source_fingerprint=version.source_fingerprint,
            dpp_version_id=version.id,
        )

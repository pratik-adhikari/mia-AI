"""Integrate background-research evidence into the authoritative mapping state."""

from __future__ import annotations

from dataclasses import dataclass

from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.canonical import sha256_json
from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.evidence_merge import merge_packages
from mia_dpp.domain.mapping_merge import merge_mapping_results
from mia_dpp.domain.mappings import (
    EvidenceOutcomeStatus,
    MappingResult,
    MappingStatus,
    SemanticReviewItem,
)
from mia_dpp.domain.product import BackgroundJobStatus
from mia_dpp.domain.product_work import ProductWorkStage
from mia_dpp.domain.targets import TemplateIndex
from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.runtime.run_context import RunContext
from mia_dpp.runtime.run_store import RunStore
from mia_dpp.semantic.decision_policy import DecisionPolicySettings
from mia_dpp.semantic.jev import JevDecisionClient
from mia_dpp.semantic.jev_mapping import (
    map_new_jev_evidence,
    require_review_for_projection_collisions,
)
from mia_dpp.semantic.models import ContextScope
from mia_dpp.services.evidence_conflicts import (
    detect_review_conflicts,
    mark_conflicting_evidence,
    require_review_for_conflicts,
)
from mia_dpp.services.product_snapshot import update_product_snapshot
from mia_dpp.storage.base import ArtifactStore
from mia_dpp.tools.mapping.mapper import DeterministicWebsiteMapper
from mia_dpp.tools.mapping.models import SemanticMapper
from mia_dpp.tools.mapping.review import MappingReviewService


@dataclass(frozen=True, slots=True)
class ResearchIntegrationRequest:
    context: RunContext
    background_job_id: str
    integrated_iteration: int
    evidence_artifact_id: str
    targets_artifact_id: str
    semantic_mapping_artifact_id: str
    reviewed_mapping_artifact_id: str | None
    review_items_artifact_id: str | None
    known_source_urls: tuple[str, ...]
    expected_snapshot_version: int
    source_generation: int


@dataclass(frozen=True, slots=True)
class ResearchIntegrationResult:
    integrated_iteration: int | None = None
    background_job_id: str | None = None
    product_snapshot_version: int | None = None
    evidence_artifact_id: str | None = None
    source_fingerprint: str | None = None
    evidence_fingerprint: str | None = None
    known_source_urls: tuple[str, ...] | None = None
    semantic_mapping_artifact_id: str | None = None
    reviewed_mapping_artifact_id: str | None = None
    clear_reviewed_mapping: bool = False
    review_items_artifact_id: str | None = None
    mapping_cycle_id: str | None = None
    review_required: bool | None = None
    conflict_artifact_id: str | None = None
    conflicting_requirement_ids: tuple[str, ...] = ()


class ResearchMappingIntegrationService:
    """Merge background evidence and mapping results into one active run."""

    def __init__(
        self,
        *,
        catalogue: ProductCatalogue,
        artifacts: ArtifactStore,
        templates: OfficialTemplateRepository,
        mapping_review: MappingReviewService,
        semantic_mapper: SemanticMapper | None,
        jev_decider: JevDecisionClient | None,
        jev_mapping_enabled: bool,
        jev_routing_scopes: tuple[ContextScope, ...],
        jev_routing_max_concurrency: int,
        jev_decision_policy: DecisionPolicySettings,
    ) -> None:
        self._catalogue = catalogue
        self._artifacts = artifacts
        self._templates = templates
        self._mapping_review = mapping_review
        self._semantic_mapper = semantic_mapper
        self._jev_decider = jev_decider
        self._jev_mapping_enabled = jev_mapping_enabled
        self._jev_routing_scopes = jev_routing_scopes
        self._jev_routing_max_concurrency = jev_routing_max_concurrency
        self._jev_decision_policy = jev_decision_policy

    async def integrate(
        self,
        request: ResearchIntegrationRequest,
    ) -> ResearchIntegrationResult | None:
        job = self._catalogue.get_background_job(
            request.background_job_id,
            user_id=request.context.user_id,
        )
        if job is None:
            return None
        if (
            job.status in {BackgroundJobStatus.QUEUED, BackgroundJobStatus.RUNNING}
            and int(job.metadata.get("iteration", 0)) <= request.integrated_iteration
        ):
            return None
        if job.status not in {
            BackgroundJobStatus.COMPLETED,
            BackgroundJobStatus.QUEUED,
            BackgroundJobStatus.RUNNING,
        }:
            return None

        evidence_artifact = job.metadata.get("researchEvidenceArtifactId")
        if not isinstance(evidence_artifact, str):
            return None
        iteration = int(job.metadata.get("iteration", 0))
        if iteration <= request.integrated_iteration:
            return ResearchIntegrationResult(
                integrated_iteration=request.integrated_iteration,
            )

        work = RunStore(request.context, self._catalogue, self._artifacts)
        existing_package = work.load(
            request.evidence_artifact_id,
            ProductKnowledgePackage,
        )
        research_package = work.load(evidence_artifact, ProductKnowledgePackage)
        merged_package = merge_packages(
            existing_package,
            research_package,
            preserve_existing=True,
        )
        known_ids = {item.id for item in existing_package.evidence}
        new_ids = {item.id for item in merged_package.evidence} - known_ids
        if not new_ids:
            snapshot = update_product_snapshot(
                self._catalogue,
                request.context,
                ProductWorkStage.MAPPING,
                expected_version=request.expected_snapshot_version,
                source_generation=request.source_generation,
                last_integrated_research_job_id=job.id,
            )
            return ResearchIntegrationResult(
                background_job_id=job.id,
                integrated_iteration=iteration,
                product_snapshot_version=snapshot.version,
            )

        current_mapping_id = (
            request.reviewed_mapping_artifact_id or request.semantic_mapping_artifact_id
        )
        current_mapping = work.load(current_mapping_id, MappingResult)
        index = work.load(request.targets_artifact_id, TemplateIndex)
        incremental_mapping_id = job.metadata.get(
            "integratedMappingArtifactId"
        ) or job.metadata.get("newMappingArtifactId")
        if isinstance(incremental_mapping_id, str):
            incremental_mapping = work.load(incremental_mapping_id, MappingResult)
        else:
            incremental_mapping = await self._map_research_evidence(
                work,
                merged_package,
                index,
                new_ids,
                evidence_artifact_id=request.evidence_artifact_id,
            )

        merged_mapping = merge_mapping_results(current_mapping, incremental_mapping)
        missing_ids = {item.id for item in merged_package.evidence} - {
            item.evidence_id for item in merged_mapping.outcomes
        }
        if missing_ids:
            work.event(
                "research.mapping_gap.started",
                f"Mapping {len(missing_ids)} research facts deferred before targets were ready.",
                metadata={"evidenceCount": len(missing_ids), "activityKey": job.id},
            )
            repaired = await self._map_research_evidence(
                work,
                merged_package,
                index,
                missing_ids,
                evidence_artifact_id=request.evidence_artifact_id,
            )
            incremental_mapping = merge_mapping_results(incremental_mapping, repaired)
            merged_mapping = merge_mapping_results(merged_mapping, repaired)
            work.event(
                "research.mapping_gap.completed",
                f"Mapped {len(missing_ids)} previously deferred research facts.",
                metadata={"evidenceCount": len(missing_ids), "activityKey": job.id},
            )

        if self._jev_mapping_enabled:
            merged_mapping = require_review_for_projection_collisions(merged_mapping)
        conflicts = detect_review_conflicts(
            existing_package,
            research_package,
            current_mapping,
            incremental_mapping,
            index,
        )
        merged_mapping = require_review_for_conflicts(merged_mapping, index, conflicts)
        merged_package = mark_conflicting_evidence(merged_package, conflicts)

        merged_evidence_id = work.put_model(
            "evidence/product-knowledge-integrated.json",
            merged_package,
            derived_from=(request.evidence_artifact_id, evidence_artifact),
        )
        merged_mapping_id = work.put_model(
            "mapping/research-integrated.json",
            merged_mapping,
            derived_from=(current_mapping_id,),
        )

        conflict_artifact_id: str | None = None
        conflict_requirement_ids: tuple[str, ...] = ()
        mapping_cycle_id = self._mapping_review.cycle_id(
            merged_package,
            index,
            merged_mapping,
        )
        if conflicts:
            conflict_requirement_ids = tuple(
                dict.fromkeys(item.requirement_id for item in conflicts)
            )
            conflict_artifact_id = work.put_json(
                "mapping/evidence-conflicts.json",
                [item.model_dump(mode="json", by_alias=True) for item in conflicts],
                derived_from=(merged_evidence_id, merged_mapping_id),
            )

        review_evidence_ids = set(new_ids)
        for conflict in conflicts:
            review_evidence_ids.update(
                (conflict.existing_evidence_id, conflict.incoming_evidence_id)
            )
        all_reviews = self._mapping_review.complete_review(
            merged_package,
            merged_mapping,
            index,
        )
        if self._jev_mapping_enabled:
            focused_reviews = tuple(
                item.model_copy(
                    update={
                        "status": EvidenceOutcomeStatus.UNCERTAIN,
                        "review_priority": (
                            item.mapping.review_priority
                            if item.mapping.review_priority in {"confirm", "alarm"}
                            else "alarm"
                        ),
                    }
                )
                for item in all_reviews
                if item.evidence_id in review_evidence_ids
                and item.mapping is not None
                and item.mapping.status is MappingStatus.REVIEW
            )
        else:
            focused_reviews = tuple(
                item for item in all_reviews if item.evidence_id in review_evidence_ids
            )

        if not request.reviewed_mapping_artifact_id and request.review_items_artifact_id:
            try:
                prior_reviews = tuple(
                    SemanticReviewItem.model_validate(item)
                    for item in work.load_json(request.review_items_artifact_id)
                )
                existing_ids = {item.evidence_id for item in focused_reviews}
                focused_reviews = (
                    tuple(item for item in prior_reviews if item.evidence_id not in existing_ids)
                    + focused_reviews
                )
            except (KeyError, TypeError, ValueError):
                pass

        review_required = bool(focused_reviews)
        review_items_id: str | None = None
        if review_required:
            review_items_id = work.put_json(
                "mapping/review-items-research.json",
                [item.model_dump(mode="json", by_alias=True) for item in focused_reviews],
                derived_from=(
                    merged_mapping_id,
                    *((conflict_artifact_id,) if conflict_artifact_id else ()),
                ),
            )

        work.event(
            "research.integrated",
            (
                f"Integrated {len(new_ids)} new background evidence records; "
                f"{len(focused_reviews)} facts require confirmation."
                if review_required
                else (
                    f"Integrated {len(new_ids)} new background evidence records "
                    "without replacing review state."
                )
            ),
            metadata={
                "jobId": job.id,
                "evidenceAdded": len(new_ids),
                "conflicts": len(conflicts),
                "reviewItems": len(focused_reviews),
                "conflictingRequirementIds": list(conflict_requirement_ids),
            },
        )
        merged_fingerprint = sha256_json(
            {
                "sources": [item.content_sha256 for item in merged_package.acquired_sources],
                "evidence": [item.id for item in merged_package.evidence],
            }
        )
        snapshot = update_product_snapshot(
            self._catalogue,
            request.context,
            ProductWorkStage.HUMAN_REVIEW if review_required else ProductWorkStage.MAPPING,
            expected_version=request.expected_snapshot_version,
            source_generation=request.source_generation,
            evidence_artifact_id=merged_evidence_id,
            reviewed_mapping_artifact_id=(None if review_required else merged_mapping_id),
            semantic_mapping_artifact_id=merged_mapping_id,
            source_fingerprint=merged_fingerprint,
            evidence_fingerprint=merged_fingerprint,
            mapping_cycle_id=mapping_cycle_id or None,
            human_review_pending=review_required,
            conflicting_requirement_ids=conflict_requirement_ids,
            conflict_artifact_id=conflict_artifact_id,
            last_integrated_research_job_id=job.id,
        )
        known_source_urls = tuple(
            dict.fromkeys(
                (
                    *request.known_source_urls,
                    *(source.final_url for source in merged_package.acquired_sources),
                )
            )
        )
        return ResearchIntegrationResult(
            evidence_artifact_id=merged_evidence_id,
            source_fingerprint=merged_fingerprint,
            evidence_fingerprint=merged_fingerprint,
            background_job_id=job.id,
            known_source_urls=known_source_urls,
            semantic_mapping_artifact_id=merged_mapping_id,
            reviewed_mapping_artifact_id=(
                merged_mapping_id
                if not review_required and request.reviewed_mapping_artifact_id
                else None
            ),
            clear_reviewed_mapping=review_required,
            review_items_artifact_id=review_items_id,
            mapping_cycle_id=mapping_cycle_id if review_required else None,
            review_required=review_required,
            conflict_artifact_id=conflict_artifact_id,
            conflicting_requirement_ids=conflict_requirement_ids,
            product_snapshot_version=snapshot.version,
            integrated_iteration=iteration,
        )

    async def _map_research_evidence(
        self,
        work: RunStore,
        package: ProductKnowledgePackage,
        index: TemplateIndex,
        evidence_ids: set[str],
        *,
        evidence_artifact_id: str,
    ) -> MappingResult:
        incremental = package.model_copy(
            update={"evidence": tuple(item for item in package.evidence if item.id in evidence_ids)}
        )
        if self._jev_mapping_enabled:
            if self._jev_decider is None:
                raise RuntimeError("Jev mapping requires a configured Jev decider")
            result, _, routing, policy = await map_new_jev_evidence(
                package=package,
                evidence_ids=evidence_ids,
                index=index,
                templates=self._templates,
                decider=self._jev_decider,
                scopes=self._jev_routing_scopes,
                max_concurrency=self._jev_routing_max_concurrency,
                settings=self._jev_decision_policy,
            )
            routing_id = work.put_model(
                "semantic/research-deferred-jev-routing.json",
                routing,
                derived_from=(evidence_artifact_id,),
            )
            work.put_model(
                "semantic/research-deferred-jev-policy.json",
                policy,
                derived_from=(routing_id,),
            )
            return result

        mapper = self._semantic_mapper
        if mapper is None:
            raise RuntimeError(
                "background evidence remains unmapped and no semantic mapper is configured"
            )
        deterministic = await DeterministicWebsiteMapper(
            self._templates,
            index,
        ).propose(incremental.evidence)
        semantic = await mapper.map(incremental, index, deterministic)
        return self._mapping_review.apply_semantic_run(
            incremental,
            deterministic,
            index,
            semantic,
        )

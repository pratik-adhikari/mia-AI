"""Reusable target construction, mapping, and coverage operations."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from urllib.parse import urlsplit

from mia_dpp.aas.requirements import build_template_index
from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.canonical import sha256_json
from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.evidence_merge import merge_packages
from mia_dpp.domain.mapping_merge import merge_mapping_results
from mia_dpp.domain.mappings import (
    CoverageStatus,
    EvidenceOutcomeStatus,
    MappingResult,
    MappingStatus,
    SemanticReviewItem,
)
from mia_dpp.domain.product import BackgroundJobStatus
from mia_dpp.domain.product_work import ProductWorkStage
from mia_dpp.domain.targets import RequirementKind, TemplateIndex
from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.runtime.run_context import RunContext
from mia_dpp.runtime.run_store import RunStore
from mia_dpp.semantic.decision_policy import DecisionPolicyReport, DecisionPolicySettings
from mia_dpp.semantic.idta_routing import IdtaRoutingReport
from mia_dpp.semantic.jev import JevDecisionClient
from mia_dpp.semantic.jev_mapping import (
    map_jev_routes,
    map_new_jev_evidence,
    require_review_for_projection_collisions,
)
from mia_dpp.semantic.models import ContextScope
from mia_dpp.services.evidence_conflicts import (
    detect_review_conflicts,
    mark_conflicting_evidence,
    require_review_for_conflicts,
)
from mia_dpp.services.product_snapshot import (
    model_fingerprint,
    semantic_mapper_fingerprint,
    update_product_snapshot,
)
from mia_dpp.services.reconfirmation import ReviewReuseStatus, review_reuse_status
from mia_dpp.storage.base import ArtifactStore
from mia_dpp.tools.mapping.coverage import coverage as calculate_coverage
from mia_dpp.tools.mapping.mapper import DeterministicWebsiteMapper
from mia_dpp.tools.mapping.models import SemanticMapper
from mia_dpp.tools.mapping.review import MappingReviewService


@dataclass(frozen=True, slots=True)
class TargetBuildResult:
    artifact_id: str
    target_fingerprint: str
    product_snapshot_version: int


@dataclass(frozen=True, slots=True)
class DeterministicMappingStageResult:
    artifact_id: str
    mapping_input_fingerprint: str
    product_snapshot_version: int


@dataclass(frozen=True, slots=True)
class SemanticMappingStageResult:
    mapping_artifact_id: str
    review_items_artifact_id: str
    mapping_cycle_id: str
    review_required: bool
    product_snapshot_version: int
    reviewed_mapping_artifact_id: str | None = None
    clear_reviewed_mapping: bool = False
    semantic_mapper_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class CoverageStageResult:
    artifact_id: str
    unresolved_requirement_ids: tuple[str, ...]
    product_snapshot_version: int


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


class MappingExecutionService:
    """Run reusable mapping stages without depending on orchestration state."""

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

    def _store(self, context: RunContext) -> RunStore:
        return RunStore(context, self._catalogue, self._artifacts)

    def build_targets(
        self,
        context: RunContext,
        *,
        evidence_artifact_id: str,
        template_keys: tuple[str, ...],
        expected_snapshot_version: int,
        source_generation: int,
    ) -> TargetBuildResult:
        work = self._store(context)
        templates = tuple(self._templates.load(key) for key in template_keys)
        index = build_template_index(templates)
        artifact_id = work.put_model(
            "mapping/targets.json",
            index,
            derived_from=(evidence_artifact_id,),
        )
        target_fingerprint = model_fingerprint(index)
        snapshot = update_product_snapshot(
            self._catalogue,
            context,
            ProductWorkStage.TARGETS,
            expected_version=expected_snapshot_version,
            source_generation=source_generation,
            template_keys=tuple(item.key for item in index.selected_templates),
            template_releases=tuple(item.release for item in index.selected_templates),
            targets_artifact_id=artifact_id,
            target_fingerprint=target_fingerprint,
        )
        return TargetBuildResult(
            artifact_id=artifact_id,
            target_fingerprint=target_fingerprint,
            product_snapshot_version=snapshot.version,
        )

    async def deterministic_map(
        self,
        context: RunContext,
        *,
        evidence_artifact_id: str,
        targets_artifact_id: str,
        evidence_fingerprint: str | None,
        source_fingerprint: str | None,
        target_fingerprint: str | None,
        expected_snapshot_version: int,
        source_generation: int,
    ) -> DeterministicMappingStageResult:
        work = self._store(context)
        package = work.load(evidence_artifact_id, ProductKnowledgePackage)
        index = work.load(targets_artifact_id, TemplateIndex)
        started = perf_counter()
        result = await DeterministicWebsiteMapper(self._templates, index).propose(package.evidence)
        duration_ms = round((perf_counter() - started) * 1000, 2)
        artifact_id = work.put_model(
            "mapping/deterministic.json",
            result,
            derived_from=(evidence_artifact_id, targets_artifact_id),
        )
        work.event(
            "mapping.deterministic.completed",
            "Completed deterministic mapping for the currently available evidence.",
            metadata={
                "durationMs": duration_ms,
                "evidenceCount": len(package.evidence),
                "mapped": len(result.mapped),
                "ambiguous": len(result.ambiguous),
                "unmatched": len(result.unmatched_evidence_ids),
            },
        )
        mapping_input_fingerprint = model_fingerprint(
            {
                "evidenceFingerprint": evidence_fingerprint or source_fingerprint,
                "targetFingerprint": target_fingerprint,
                "deterministic": result.model_dump(mode="json", by_alias=True),
            }
        )
        snapshot = update_product_snapshot(
            self._catalogue,
            context,
            ProductWorkStage.MAPPING,
            expected_version=expected_snapshot_version,
            source_generation=source_generation,
            deterministic_mapping_artifact_id=artifact_id,
            mapping_input_fingerprint=mapping_input_fingerprint,
        )
        return DeterministicMappingStageResult(
            artifact_id=artifact_id,
            mapping_input_fingerprint=mapping_input_fingerprint,
            product_snapshot_version=snapshot.version,
        )

    async def semantic_map(
        self,
        context: RunContext,
        *,
        evidence_artifact_id: str,
        targets_artifact_id: str,
        deterministic_mapping_artifact_id: str,
        reviewed_mapping_artifact_id: str | None,
        reuse_prior_work: bool,
        seeded_from_run_id: str | None,
        evidence_fingerprint: str | None,
        source_fingerprint: str | None,
        target_fingerprint: str | None,
        template_keys: tuple[str, ...],
        jev_routing_artifact_id: str | None,
        jev_policy_artifact_id: str | None,
        expected_snapshot_version: int,
        source_generation: int,
    ) -> SemanticMappingStageResult:
        work = self._store(context)
        package = work.load(evidence_artifact_id, ProductKnowledgePackage)
        index = work.load(targets_artifact_id, TemplateIndex)
        deterministic = work.load(deterministic_mapping_artifact_id, MappingResult)

        durable_snapshot = self._catalogue.get_product_work_snapshot(
            context.product_id,
            user_id=context.user_id,
        )
        reuse_status = review_reuse_status(
            durable_snapshot,
            evidence_fingerprint=evidence_fingerprint or source_fingerprint,
            target_fingerprint=target_fingerprint,
        )
        if (
            reuse_prior_work
            and reviewed_mapping_artifact_id
            and reuse_status is not ReviewReuseStatus.STALE
        ):
            try:
                reused = work.load(reviewed_mapping_artifact_id, MappingResult)
                self._mapping_review.validate_complete_accounting(package, reused)
            except (KeyError, ValueError):
                reused = None
            if reused is not None and _mapping_targets_are_current(reused, index):
                cycle_id = self._mapping_review.cycle_id(package, index, reused)
                reviews = (
                    ()
                    if reuse_status is ReviewReuseStatus.CURRENT
                    else self._mapping_review.complete_review(package, reused, index)
                )
                mapping_id = work.put_model(
                    "mapping/reused-reviewed.json",
                    reused,
                    derived_from=(reviewed_mapping_artifact_id, evidence_artifact_id),
                )
                review_id = work.put_json(
                    "mapping/review-items.json",
                    [item.model_dump(mode="json", by_alias=True) for item in reviews],
                    derived_from=(mapping_id,),
                )
                work.event(
                    "mapping.history_reused",
                    (
                        f"Reused {len(reused.mapped)} human-reviewed mappings "
                        "without semantic remapping."
                        if not reviews
                        else (
                            f"Reused {len(reused.mapped)} prior mappings and reopened "
                            "them for confirmation."
                        )
                    ),
                    metadata={
                        "seededFromRunId": seeded_from_run_id,
                        "priorMappingArtifactId": reviewed_mapping_artifact_id,
                        "reviewReuseStatus": reuse_status.value,
                    },
                )
                snapshot = update_product_snapshot(
                    self._catalogue,
                    context,
                    ProductWorkStage.HUMAN_REVIEW if reviews else ProductWorkStage.MAPPING,
                    expected_version=expected_snapshot_version,
                    source_generation=source_generation,
                    semantic_mapping_artifact_id=mapping_id,
                    reviewed_mapping_artifact_id=(mapping_id if not reviews else None),
                    mapping_cycle_id=cycle_id,
                    human_review_pending=bool(reviews),
                )
                return SemanticMappingStageResult(
                    mapping_artifact_id=mapping_id,
                    review_items_artifact_id=review_id,
                    mapping_cycle_id=cycle_id,
                    review_required=bool(reviews),
                    reviewed_mapping_artifact_id=mapping_id if not reviews else None,
                    clear_reviewed_mapping=bool(reviews),
                    product_snapshot_version=snapshot.version,
                )

        if self._jev_mapping_enabled:
            if not jev_routing_artifact_id or not jev_policy_artifact_id:
                raise KeyError("Jev mapping requires routing and decision-policy artifacts")
            routing = work.load(jev_routing_artifact_id, IdtaRoutingReport)
            policy = work.load(jev_policy_artifact_id, DecisionPolicyReport)
            result, reviews = map_jev_routes(
                package,
                index,
                routing,
                policy,
                self._templates,
            )
            self._mapping_review.validate_complete_accounting(package, result)
            mapping_id = work.put_model(
                "mapping/jev-fixed-targets.json",
                result,
                derived_from=(
                    evidence_artifact_id,
                    targets_artifact_id,
                    jev_policy_artifact_id,
                ),
            )
            review_id = work.put_json(
                "mapping/review-items.json",
                [item.model_dump(mode="json", by_alias=True) for item in reviews],
                derived_from=(mapping_id,),
            )
            cycle_id = self._mapping_review.cycle_id(package, index, result)
            work.event(
                "mapping.jev_completed",
                (
                    f"Jev routed {len(result.mapped)} facts to fixed targets; "
                    f"{len(reviews)} require review and {len(result.unmatched_evidence_ids)} "
                    "remain source-backed and unmapped."
                ),
                metadata={
                    "mapped": len(result.mapped),
                    "review": len(reviews),
                    "unmapped": len(result.unmatched_evidence_ids),
                    "reviewPriorities": {
                        priority: sum(
                            item.review_priority == priority
                            for item in (*result.mapped, *result.ambiguous)
                        )
                        for priority in ("auto", "optional", "confirm", "alarm")
                    },
                },
            )
            mapper_fingerprint = model_fingerprint(
                {
                    "model": "jev-idta",
                    "policy": policy.settings.model_dump(mode="json", by_alias=True),
                }
            )
            snapshot = update_product_snapshot(
                self._catalogue,
                context,
                ProductWorkStage.HUMAN_REVIEW if reviews else ProductWorkStage.MAPPING,
                expected_version=expected_snapshot_version,
                source_generation=source_generation,
                semantic_mapping_artifact_id=mapping_id,
                semantic_mapper_fingerprint=mapper_fingerprint,
                mapping_cycle_id=cycle_id,
                human_review_pending=bool(reviews),
            )
            return SemanticMappingStageResult(
                mapping_artifact_id=mapping_id,
                review_items_artifact_id=review_id,
                mapping_cycle_id=cycle_id,
                review_required=bool(reviews),
                semantic_mapper_fingerprint=mapper_fingerprint,
                product_snapshot_version=snapshot.version,
            )

        mapper = self._semantic_mapper
        if mapper is None:
            raise RuntimeError("semantic mapping requires a configured model")
        work.event(
            "mapping.semantic.started",
            f"Mapping {len(package.evidence)} source facts against the selected IDTA templates.",
            metadata={"evidenceCount": len(package.evidence), "activityKey": "semantic-mapping"},
        )
        product = self._catalogue.get_product(context.product_id, user_id=context.user_id)
        domain = (urlsplit(product.canonical_url).hostname or "") if product else None
        knowledge = tuple(
            {
                "sourceField": item.source_field,
                "targetTemplate": item.target_template,
                "targetPath": list(item.target_path),
                "semanticId": item.semantic_id,
                "confirmations": item.confirmations,
                "humanComments": list(item.human_comments),
            }
            for record in package.evidence
            for item in self._catalogue.relevant_mapping_knowledge(
                record.source_label or record.predicate,
                manufacturer=product.manufacturer if product else None,
                domain=domain,
                template_keys=template_keys,
                user_id=context.user_id,
            )
        )
        semantic_started = perf_counter()
        semantic_run = await mapper.map(
            package,
            index,
            deterministic,
            reviewed_knowledge=knowledge,
        )
        semantic_duration_ms = round((perf_counter() - semantic_started) * 1000, 2)
        result = self._mapping_review.apply_semantic_run(
            package,
            deterministic,
            index,
            semantic_run,
        )
        cycle_id = self._mapping_review.cycle_id(package, index, result)
        reviews = self._mapping_review.complete_review(package, result, index)
        semantic_id = work.put_model(
            "mapping/semantic-run.json",
            semantic_run,
            derived_from=(deterministic_mapping_artifact_id,),
        )
        mapping_id = work.put_model(
            "mapping/mapping.json",
            result,
            derived_from=(semantic_id,),
        )
        review_id = work.put_json(
            "mapping/review-items.json",
            [item.model_dump(mode="json", by_alias=True) for item in reviews],
            derived_from=(mapping_id,),
        )
        work.event(
            "mapping.completed",
            f"Prepared {len(reviews)} source-derived mapping decisions for review.",
            metadata={
                "mapped": len(result.mapped),
                "ambiguous": len(result.ambiguous),
                "unmatched": len(result.unmatched_evidence_ids),
                "semanticModelRequests": semantic_run.metrics.model_requests,
                "durationMs": semantic_duration_ms,
                "evidenceCount": len(package.evidence),
                "activityKey": "semantic-mapping",
            },
        )
        mapper_fingerprint = semantic_mapper_fingerprint(mapper)
        snapshot = update_product_snapshot(
            self._catalogue,
            context,
            ProductWorkStage.HUMAN_REVIEW if reviews else ProductWorkStage.MAPPING,
            expected_version=expected_snapshot_version,
            source_generation=source_generation,
            semantic_mapping_artifact_id=mapping_id,
            semantic_mapper_fingerprint=mapper_fingerprint,
            mapping_cycle_id=cycle_id,
            human_review_pending=bool(reviews),
        )
        return SemanticMappingStageResult(
            mapping_artifact_id=mapping_id,
            review_items_artifact_id=review_id,
            mapping_cycle_id=cycle_id,
            review_required=bool(reviews),
            semantic_mapper_fingerprint=mapper_fingerprint,
            product_snapshot_version=snapshot.version,
        )

    def coverage(
        self,
        context: RunContext,
        *,
        evidence_artifact_id: str,
        targets_artifact_id: str,
        mapping_artifact_id: str,
        expected_snapshot_version: int,
        source_generation: int,
    ) -> CoverageStageResult:
        work = self._store(context)
        package = work.load(evidence_artifact_id, ProductKnowledgePackage)
        index = work.load(targets_artifact_id, TemplateIndex)
        result = work.load(mapping_artifact_id, MappingResult)
        report = calculate_coverage(package, index, mapping_result=result)
        artifact_id = work.put_model(
            "mapping/coverage.json",
            report,
            derived_from=(mapping_artifact_id,),
        )
        requirements = {item.id: item for item in report.inventory.requirements}
        unresolved = tuple(
            item.requirement_id
            for item in report.coverage
            if requirements[item.requirement_id].required
            and requirements[item.requirement_id].kind is RequirementKind.VALUE
            and item.status is not CoverageStatus.SATISFIED
        )
        work.event(
            "coverage.completed",
            f"Coverage has {len(unresolved)} unresolved mandatory value requirements.",
            metadata={"requiredUnresolved": len(unresolved)},
        )
        snapshot = update_product_snapshot(
            self._catalogue,
            context,
            ProductWorkStage.COVERAGE,
            expected_version=expected_snapshot_version,
            source_generation=source_generation,
            coverage_artifact_id=artifact_id,
            unresolved_required_ids=unresolved,
        )
        return CoverageStageResult(
            artifact_id=artifact_id,
            unresolved_requirement_ids=unresolved,
            product_snapshot_version=snapshot.version,
        )


    async def integrate_background_research(
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

        work = self._store(request.context)
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
            request.reviewed_mapping_artifact_id
            or request.semantic_mapping_artifact_id
        )
        current_mapping = work.load(current_mapping_id, MappingResult)
        index = work.load(request.targets_artifact_id, TemplateIndex)
        incremental_mapping_id = (
            job.metadata.get("integratedMappingArtifactId")
            or job.metadata.get("newMappingArtifactId")
        )
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
                focused_reviews = tuple(
                    item for item in prior_reviews if item.evidence_id not in existing_ids
                ) + focused_reviews
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
                "sources": [
                    item.content_sha256 for item in merged_package.acquired_sources
                ],
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
            update={
                "evidence": tuple(
                    item for item in package.evidence if item.id in evidence_ids
                )
            }
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


def _mapping_targets_are_current(mapping: MappingResult, index: TemplateIndex) -> bool:
    valid_targets = {
        (item.template_key, item.template_release, item.template_path)
        for item in index.requirements
    }
    return all(
        (item.target.template_key, item.target.template_release, item.target.template_path)
        in valid_targets
        for item in (*mapping.mapped, *mapping.ambiguous, *mapping.rejected)
    )

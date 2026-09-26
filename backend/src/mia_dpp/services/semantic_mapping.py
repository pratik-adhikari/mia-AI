"""Reusable semantic mapping over prepared evidence and template targets."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from urllib.parse import urlsplit

from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.mappings import MappingResult
from mia_dpp.domain.product_work import ProductWorkStage
from mia_dpp.domain.targets import TemplateIndex
from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.runtime.run_context import RunContext
from mia_dpp.runtime.run_store import RunStore
from mia_dpp.semantic.decision_policy import DecisionPolicyReport
from mia_dpp.semantic.idta_routing import IdtaRoutingReport
from mia_dpp.semantic.jev_mapping import map_jev_routes
from mia_dpp.services.product_snapshot import (
    model_fingerprint,
    semantic_mapper_fingerprint,
    update_product_snapshot,
)
from mia_dpp.services.reconfirmation import ReviewReuseStatus, review_reuse_status
from mia_dpp.storage.base import ArtifactStore
from mia_dpp.tools.mapping.models import SemanticMapper
from mia_dpp.tools.mapping.review import MappingReviewService


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


class SemanticMappingService:
    """Map prepared evidence without depending on graph state or routing."""

    def __init__(
        self,
        *,
        catalogue: ProductCatalogue,
        artifacts: ArtifactStore,
        templates: OfficialTemplateRepository,
        mapping_review: MappingReviewService,
        semantic_mapper: SemanticMapper | None,
        jev_mapping_enabled: bool,
    ) -> None:
        self._catalogue = catalogue
        self._artifacts = artifacts
        self._templates = templates
        self._mapping_review = mapping_review
        self._semantic_mapper = semantic_mapper
        self._jev_mapping_enabled = jev_mapping_enabled

    async def map(
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
        work = RunStore(context, self._catalogue, self._artifacts)
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

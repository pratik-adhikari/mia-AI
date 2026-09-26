"""Apply validated human mapping/value decisions without owning orchestration interrupts."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from mia_dpp.domain.evidence import EvidenceRecord, ProductKnowledgePackage
from mia_dpp.domain.mappings import (
    CoverageStatus,
    MappingResult,
    SemanticReviewItem,
)
from mia_dpp.domain.product import RunStatus
from mia_dpp.domain.product_work import ProductWorkStage
from mia_dpp.domain.targets import TemplateIndex
from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.runtime.run_context import RunContext
from mia_dpp.runtime.run_store import RunStore
from mia_dpp.services.human_review_audit import mapping_review_records, supplied_value_record
from mia_dpp.services.human_submission import HumanValueSubmission, MappingReviewSubmission
from mia_dpp.services.product_snapshot import model_fingerprint, update_product_snapshot
from mia_dpp.storage.base import ArtifactStore
from mia_dpp.tools.mapping.coverage import coverage as calculate_coverage
from mia_dpp.tools.mapping.review import MappingReviewService


@dataclass(frozen=True, slots=True)
class AppliedReviewResult:
    evidence_artifact_id: str
    reviewed_mapping_artifact_id: str
    review_fingerprint: str
    product_snapshot_version: int


@dataclass(frozen=True, slots=True)
class AppliedHumanValueResult:
    evidence_artifact_id: str
    reviewed_mapping_artifact_id: str
    review_fingerprint: str
    product_snapshot_version: int


class MappingHumanService:
    """Persist trusted human changes after an orchestrator resumes."""

    def __init__(
        self,
        *,
        catalogue: ProductCatalogue,
        artifacts: ArtifactStore,
        mapping_review: MappingReviewService,
    ) -> None:
        self._catalogue = catalogue
        self._artifacts = artifacts
        self._mapping_review = mapping_review

    def _store(self, context: RunContext) -> RunStore:
        return RunStore(context, self._catalogue, self._artifacts)

    def apply_review(
        self,
        context: RunContext,
        *,
        submission: MappingReviewSubmission,
        reviews: tuple[SemanticReviewItem, ...],
        evidence_artifact_id: str,
        targets_artifact_id: str,
        semantic_mapping_artifact_id: str,
        mapping_cycle_id: str,
        conflicting_requirement_ids: tuple[str, ...],
        evidence_fingerprint: str | None,
        source_fingerprint: str | None,
        target_fingerprint: str | None,
        mapping_input_fingerprint: str | None,
        expected_snapshot_version: int,
        source_generation: int,
    ) -> AppliedReviewResult:
        work = self._store(context)
        by_id = {item.id: item for item in reviews}
        if {item.review_id for item in submission.decisions} != set(by_id):
            raise ValueError("mapping review must contain exactly one decision for every row")

        package = work.load(evidence_artifact_id, ProductKnowledgePackage)
        index = work.load(targets_artifact_id, TemplateIndex)
        result = work.load(semantic_mapping_artifact_id, MappingResult)
        reviewed: list[SemanticReviewItem] = []

        for decision in submission.decisions:
            before = by_id[decision.review_id]
            proposed_record = next(
                record for record in package.evidence if record.id == before.evidence_id
            )
            package, result, item = self._mapping_review.decide(
                package,
                result,
                index,
                before,
                decision=decision.decision,
                thread_id=context.thread_id,
                corrected_requirement_id=decision.corrected_requirement_id,
                corrected_semantic_id=decision.corrected_semantic_id,
                corrected_value=decision.corrected_value,
                comment=decision.comment,
                actor_name=submission.actor_name,
            )
            reviewed.append(item)
            for audit in mapping_review_records(
                user_id=context.user_id,
                product_id=context.product_id,
                run_id=context.run_id,
                thread_id=context.thread_id,
                mapping_cycle_id=mapping_cycle_id,
                actor_name=submission.actor_name,
                decision=decision,
                before=before,
                after=item,
                proposed_value=_audit_evidence_value(proposed_record),
                final_value=_audit_evidence_value(
                    next(record for record in package.evidence if record.id == item.evidence_id)
                ),
            ):
                self._catalogue.add_human_review(audit)
            if item.mapping is not None:
                product = self._catalogue.get_product(
                    context.product_id,
                    user_id=context.user_id,
                )
                domain = (urlsplit(product.canonical_url).hostname or "") if product else None
                self._catalogue.remember_mapping_review(
                    item.mapping,
                    decision=decision.decision,
                    manufacturer=product.manufacturer if product else None,
                    domain=domain,
                    product_family=None,
                    comment=decision.comment,
                    actor_name=submission.actor_name,
                    user_id=context.user_id,
                    run_id=context.run_id,
                )

        self._mapping_review.validate_projection_uniqueness(result)

        evidence_id = work.put_model(
            "evidence/product-knowledge-reviewed.json",
            package,
            derived_from=(evidence_artifact_id,),
        )
        mapping_id = work.put_model(
            "mapping/reviewed.json",
            result,
            derived_from=(semantic_mapping_artifact_id,),
        )
        work.put_json(
            "mapping/review-decisions.json",
            {
                "mappingCycleId": mapping_cycle_id,
                "actorName": submission.actor_name,
                "decisions": [item.to_wire_dict() for item in submission.decisions],
                "result": [item.model_dump(mode="json", by_alias=True) for item in reviewed],
            },
            derived_from=(mapping_id,),
        )

        conflict_ids = set(conflicting_requirement_ids)
        if conflict_ids:
            resolved_report = calculate_coverage(package, index, mapping_result=result)
            unresolved_conflicts = tuple(
                item.requirement_id
                for item in resolved_report.coverage
                if item.requirement_id in conflict_ids
                and item.status is not CoverageStatus.SATISFIED
            )
            if unresolved_conflicts:
                raise ValueError(
                    "conflicting evidence requires one resolved value for: "
                    + ", ".join(unresolved_conflicts)
                )

        self._catalogue.set_run_status(context.run_id, RunStatus.RUNNING)
        work.event(
            "mapping.review_completed",
            "Applied trusted human decisions to the complete mapping cycle.",
        )
        review_fingerprint = model_fingerprint(
            {
                "cycle": mapping_cycle_id,
                "mapping": result.model_dump(mode="json", by_alias=True),
            }
        )
        snapshot = update_product_snapshot(
            self._catalogue,
            context,
            ProductWorkStage.HUMAN_REVIEW,
            expected_version=expected_snapshot_version,
            source_generation=source_generation,
            evidence_artifact_id=evidence_id,
            reviewed_mapping_artifact_id=mapping_id,
            review_fingerprint=review_fingerprint,
            reviewed_evidence_fingerprint=evidence_fingerprint or source_fingerprint,
            reviewed_target_fingerprint=target_fingerprint,
            reviewed_mapping_input_fingerprint=mapping_input_fingerprint,
            mapping_cycle_id=mapping_cycle_id,
            human_review_pending=False,
            conflicting_requirement_ids=(),
            conflict_artifact_id=None,
        )
        return AppliedReviewResult(
            evidence_artifact_id=evidence_id,
            reviewed_mapping_artifact_id=mapping_id,
            review_fingerprint=review_fingerprint,
            product_snapshot_version=snapshot.version,
        )

    def record_human_value(
        self,
        context: RunContext,
        *,
        submission: HumanValueSubmission,
        requirement_id: str,
        question: str,
        evidence_artifact_id: str,
        targets_artifact_id: str,
        mapping_artifact_id: str,
        missing_requirement_ids: tuple[str, ...],
        evidence_fingerprint: str | None,
        source_fingerprint: str | None,
        target_fingerprint: str | None,
        mapping_input_fingerprint: str | None,
        expected_snapshot_version: int,
        source_generation: int,
    ) -> AppliedHumanValueResult:
        work = self._store(context)
        package = work.load(evidence_artifact_id, ProductKnowledgePackage)
        index = work.load(targets_artifact_id, TemplateIndex)
        result = work.load(mapping_artifact_id, MappingResult)
        package, result = self._mapping_review.record_human_value(
            package,
            result,
            index,
            requirement_id=requirement_id,
            value=submission.value,
            thread_id=context.thread_id,
            actor_name=submission.actor_name,
            use_dummy=submission.use_dummy,
        )
        supplied_mapping = result.mapped[-1]
        supplied_evidence = package.evidence[-1]
        self._catalogue.add_human_review(
            supplied_value_record(
                user_id=context.user_id,
                product_id=context.product_id,
                run_id=context.run_id,
                thread_id=context.thread_id,
                requirement_id=requirement_id,
                evidence_id=supplied_evidence.id,
                mapping_id=supplied_mapping.id,
                actor_name=submission.actor_name,
                use_dummy=submission.use_dummy,
                final_value=_audit_evidence_value(supplied_evidence),
                final_target_path=supplied_mapping.target.template_path,
            )
        )
        evidence_id = work.put_model(
            "evidence/product-knowledge-human.json",
            package,
            derived_from=(evidence_artifact_id,),
        )
        reviewed_id = work.put_model(
            "mapping/human-value.json",
            result,
            derived_from=(mapping_artifact_id,),
        )
        self._catalogue.set_run_status(context.run_id, RunStatus.RUNNING)
        work.event("human.value_recorded", question, metadata={"requirementId": requirement_id})
        review_fingerprint = model_fingerprint(result)
        snapshot = update_product_snapshot(
            self._catalogue,
            context,
            ProductWorkStage.HUMAN_INPUT,
            expected_version=expected_snapshot_version,
            source_generation=source_generation,
            evidence_artifact_id=evidence_id,
            reviewed_mapping_artifact_id=reviewed_id,
            review_fingerprint=review_fingerprint,
            reviewed_evidence_fingerprint=evidence_fingerprint or source_fingerprint,
            reviewed_target_fingerprint=target_fingerprint,
            reviewed_mapping_input_fingerprint=mapping_input_fingerprint,
            unresolved_required_ids=tuple(
                item for item in missing_requirement_ids if item != requirement_id
            ),
        )
        return AppliedHumanValueResult(
            evidence_artifact_id=evidence_id,
            reviewed_mapping_artifact_id=reviewed_id,
            review_fingerprint=review_fingerprint,
            product_snapshot_version=snapshot.version,
        )


def _audit_evidence_value(record: EvidenceRecord) -> str:
    value = str(record.value)
    unit = record.unit
    return f"{value} {unit}".strip() if unit else value

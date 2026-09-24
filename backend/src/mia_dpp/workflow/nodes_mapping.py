"""Template, deterministic mapping, semantic mapping, HITL review, and coverage nodes."""

from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

from mia_dpp.aas.requirements import build_template_index
from mia_dpp.canonical import sha256_json
from mia_dpp.agent.models import AgentReviewRequest, AgentValueRequest
from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.mappings import CoverageStatus, MappingResult, SemanticReviewItem
from mia_dpp.domain.product import BackgroundJobStatus, RunStatus
from mia_dpp.domain.product_work import ProductWorkStage
from mia_dpp.domain.targets import RequirementKind, TemplateIndex
from mia_dpp.services.deep_research import merge_mapping_results
from mia_dpp.services.evidence_conflicts import (
    detect_review_conflicts,
    mark_conflicting_evidence,
    require_review_for_conflicts,
)
from mia_dpp.services.human_review_audit import mapping_review_records, supplied_value_record
from mia_dpp.services.reconfirmation import ReviewReuseStatus, review_reuse_status
from mia_dpp.tools.mapping.coverage import coverage as calculate_coverage
from mia_dpp.tools.mapping.mapper import DeterministicWebsiteMapper
from mia_dpp.workflow.context import MiaContext
from mia_dpp.workflow.nodes_product import merge_packages
from mia_dpp.workflow.product_snapshot import (
    model_fingerprint,
    semantic_mapper_fingerprint,
    update_product_snapshot,
)
from mia_dpp.workflow.state import MiaWorkflowState
from mia_dpp.workflow.workspace import RunWorkspace


async def build_targets(
    state: MiaWorkflowState,
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    work = RunWorkspace(state, runtime.context)
    templates = tuple(
        work.ctx.templates.load(key)
        for key in state.get("target_submodels", ("digital_nameplate", "technical_data"))
    )
    index = build_template_index(templates)
    artifact_id = work.put_model(
        "mapping/targets.json",
        index,
        derived_from=(work.state_id("evidence_artifact_id"),),
    )
    target_fingerprint = model_fingerprint(index)
    snapshot = update_product_snapshot(
        work,
        ProductWorkStage.TARGETS,
        template_keys=tuple(item.key for item in index.selected_templates),
        template_releases=tuple(item.release for item in index.selected_templates),
        targets_artifact_id=artifact_id,
        target_fingerprint=target_fingerprint,
    )
    return {
        "targets_artifact_id": artifact_id,
        "target_fingerprint": target_fingerprint,
        "product_snapshot_version": snapshot.version,
    }


async def deterministic_mapping(
    state: MiaWorkflowState,
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    work = RunWorkspace(state, runtime.context)
    package = work.load_state("evidence_artifact_id", ProductKnowledgePackage)
    index = work.load_state("targets_artifact_id", TemplateIndex)
    started = perf_counter()
    result = await DeterministicWebsiteMapper(work.ctx.templates, index).propose(package.evidence)
    duration_ms = round((perf_counter() - started) * 1000, 2)
    artifact_id = work.put_model(
        "mapping/deterministic.json",
        result,
        derived_from=(work.state_id("evidence_artifact_id"), work.state_id("targets_artifact_id")),
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
            "evidenceFingerprint": state.get("evidence_fingerprint") or state.get("source_fingerprint"),
            "targetFingerprint": state.get("target_fingerprint"),
            "deterministic": result.model_dump(mode="json", by_alias=True),
        }
    )
    snapshot = update_product_snapshot(
        work,
        ProductWorkStage.MAPPING,
        deterministic_mapping_artifact_id=artifact_id,
        mapping_input_fingerprint=mapping_input_fingerprint,
    )
    return {
        "deterministic_mapping_artifact_id": artifact_id,
        "mapping_input_fingerprint": mapping_input_fingerprint,
        "product_snapshot_version": snapshot.version,
    }


async def semantic_mapping(
    state: MiaWorkflowState,
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    work = RunWorkspace(state, runtime.context)
    package = work.load_state("evidence_artifact_id", ProductKnowledgePackage)
    index = work.load_state("targets_artifact_id", TemplateIndex)
    deterministic = work.load_state("deterministic_mapping_artifact_id", MappingResult)

    reusable_mapping_id = state.get("reviewed_mapping_artifact_id")
    durable_snapshot = work.ctx.catalogue.get_product_work_snapshot(
        work.product_id,
        user_id=work.user_id,
    )
    reuse_status = review_reuse_status(
        durable_snapshot,
        evidence_fingerprint=state.get("evidence_fingerprint") or state.get("source_fingerprint"),
        target_fingerprint=state.get("target_fingerprint"),
    )
    if state.get("reuse_prior_work") and reusable_mapping_id and reuse_status is not ReviewReuseStatus.STALE:
        try:
            reused = work.load(str(reusable_mapping_id), MappingResult)
            work.ctx.mapping_review.validate_complete_accounting(package, reused)
        except (KeyError, ValueError):
            reused = None
        if reused is not None and _mapping_targets_are_current(reused, index):
            cycle_id = work.ctx.mapping_review.cycle_id(package, index, reused)
            reviews = (
                ()
                if reuse_status is ReviewReuseStatus.CURRENT
                else work.ctx.mapping_review.complete_review(package, reused, index)
            )
            mapping_id = work.put_model(
                "mapping/reused-reviewed.json",
                reused,
                derived_from=(str(reusable_mapping_id), work.state_id("evidence_artifact_id")),
            )
            review_id = work.put_json(
                "mapping/review-items.json",
                [item.model_dump(mode="json", by_alias=True) for item in reviews],
                derived_from=(mapping_id,),
            )
            work.event(
                "mapping.history_reused",
                (
                    f"Reused {len(reused.mapped)} human-reviewed mappings without semantic remapping."
                    if not reviews
                    else f"Reused {len(reused.mapped)} prior mappings and reopened them for confirmation."
                ),
                metadata={
                    "seededFromRunId": state.get("seeded_from_run_id"),
                    "priorMappingArtifactId": str(reusable_mapping_id),
                    "reviewReuseStatus": reuse_status.value,
                },
            )
            snapshot = update_product_snapshot(
                work,
                ProductWorkStage.HUMAN_REVIEW if reviews else ProductWorkStage.MAPPING,
                semantic_mapping_artifact_id=mapping_id,
                reviewed_mapping_artifact_id=(mapping_id if not reviews else None),
                mapping_cycle_id=cycle_id,
                human_review_pending=bool(reviews),
            )
            return {
                "semantic_mapping_artifact_id": mapping_id,
                "review_items_artifact_id": review_id,
                "mapping_cycle_id": cycle_id,
                "review_required": bool(reviews),
                "reviewed_mapping_artifact_id": mapping_id if not reviews else "",
                "product_snapshot_version": snapshot.version,
            }

    mapper = work.ctx.semantic_mapper
    if mapper is None:
        raise RuntimeError("semantic mapping requires a configured model")
    product = work.ctx.catalogue.get_product(work.product_id, user_id=work.user_id)
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
        for item in work.ctx.catalogue.relevant_mapping_knowledge(
            record.source_label or record.predicate,
            manufacturer=product.manufacturer if product else None,
            domain=domain,
            template_keys=state.get("target_submodels", ("digital_nameplate", "technical_data")),
            user_id=work.user_id,
        )
    )
    semantic_started = perf_counter()
    semantic_run = await mapper.map(package, index, deterministic, reviewed_knowledge=knowledge)
    semantic_duration_ms = round((perf_counter() - semantic_started) * 1000, 2)
    result = work.ctx.mapping_review.apply_semantic_run(
        package,
        deterministic,
        index,
        semantic_run,
    )
    cycle_id = work.ctx.mapping_review.cycle_id(package, index, result)
    reviews = work.ctx.mapping_review.complete_review(package, result, index)
    semantic_id = work.put_model(
        "mapping/semantic-run.json",
        semantic_run,
        derived_from=(work.state_id("deterministic_mapping_artifact_id"),),
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
        },
    )
    mapper_fingerprint = semantic_mapper_fingerprint(mapper)
    snapshot = update_product_snapshot(
        work,
        ProductWorkStage.HUMAN_REVIEW if reviews else ProductWorkStage.MAPPING,
        semantic_mapping_artifact_id=mapping_id,
        semantic_mapper_fingerprint=mapper_fingerprint,
        mapping_cycle_id=cycle_id,
        human_review_pending=bool(reviews),
    )
    return {
        "semantic_mapping_artifact_id": mapping_id,
        "review_items_artifact_id": review_id,
        "mapping_cycle_id": cycle_id,
        "review_required": bool(reviews),
        "semantic_mapper_fingerprint": mapper_fingerprint or "",
        "product_snapshot_version": snapshot.version,
    }


def _audit_evidence_value(record: Any) -> str:
    value = str(record.value)
    return f"{value} {record.unit}".strip() if record.unit else value


async def human_review(
    state: MiaWorkflowState,
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    """Pause once for the complete mapping cycle and apply trusted decisions on resume."""

    from langgraph.types import interrupt

    work = RunWorkspace(state, runtime.context)
    reviews = tuple(
        SemanticReviewItem.model_validate(item)
        for item in work.load_json("review_items_artifact_id")
    )
    work.ctx.catalogue.set_run_status(work.run_id, RunStatus.AWAITING_HUMAN)
    payload = interrupt(
        {
            "kind": "mapping_review",
            "threadId": state["thread_id"],
            "productId": work.product_id,
            "mappingCycleId": state["mapping_cycle_id"],
            "items": [item.model_dump(mode="json", by_alias=True) for item in reviews],
        }
    )
    request = AgentReviewRequest.model_validate(payload)
    if request.thread_id != state["thread_id"] or request.product_id != work.product_id:
        raise ValueError("mapping review does not belong to this workflow")
    if request.mapping_cycle_id and request.mapping_cycle_id != state["mapping_cycle_id"]:
        raise ValueError("mapping review cycle is stale")
    by_id = {item.id: item for item in reviews}
    if {item.review_id for item in request.decisions} != set(by_id):
        raise ValueError("mapping review must contain exactly one decision for every row")

    package = work.load_state("evidence_artifact_id", ProductKnowledgePackage)
    index = work.load_state("targets_artifact_id", TemplateIndex)
    result = work.load_state("semantic_mapping_artifact_id", MappingResult)
    reviewed: list[SemanticReviewItem] = []
    for decision in request.decisions:
        before = by_id[decision.review_id]
        proposed_record = next(
            record for record in package.evidence if record.id == before.evidence_id
        )
        package, result, item = work.ctx.mapping_review.decide(
            package,
            result,
            index,
            before,
            decision=decision.decision,
            thread_id=state["thread_id"],
            corrected_requirement_id=decision.corrected_requirement_id,
            corrected_value=decision.corrected_value,
            comment=decision.comment,
            actor_name=request.actor_name,
        )
        reviewed.append(item)
        for audit in mapping_review_records(
            user_id=work.user_id,
            product_id=work.product_id,
            run_id=work.run_id,
            thread_id=state["thread_id"],
            mapping_cycle_id=state.get("mapping_cycle_id"),
            actor_name=request.actor_name,
            decision=decision,
            before=before,
            after=item,
            proposed_value=_audit_evidence_value(proposed_record),
            final_value=_audit_evidence_value(
                next(record for record in package.evidence if record.id == item.evidence_id)
            ),
        ):
            work.ctx.catalogue.add_human_review(audit)
        if item.mapping is not None:
            product = work.ctx.catalogue.get_product(work.product_id, user_id=work.user_id)
            domain = (urlsplit(product.canonical_url).hostname or "") if product else None
            work.ctx.catalogue.remember_mapping_review(
                item.mapping,
                decision=decision.decision,
                manufacturer=product.manufacturer if product else None,
                domain=domain,
                product_family=None,
                comment=decision.comment,
                actor_name=request.actor_name,
                user_id=work.user_id,
                run_id=work.run_id,
            )

    evidence_id = work.put_model(
        "evidence/product-knowledge-reviewed.json",
        package,
        derived_from=(work.state_id("evidence_artifact_id"),),
    )
    mapping_id = work.put_model(
        "mapping/reviewed.json",
        result,
        derived_from=(work.state_id("semantic_mapping_artifact_id"),),
    )
    work.put_json(
        "mapping/review-decisions.json",
        {
            "mappingCycleId": state["mapping_cycle_id"],
            "actorName": request.actor_name,
            "decisions": [
                item.model_dump(mode="json", by_alias=True) for item in request.decisions
            ],
            "result": [item.model_dump(mode="json", by_alias=True) for item in reviewed],
        },
        derived_from=(mapping_id,),
    )
    conflict_requirement_ids = set(state.get("conflicting_requirement_ids", ()))
    if conflict_requirement_ids:
        resolved_report = calculate_coverage(package, index, mapping_result=result)
        unresolved_conflicts = tuple(
            item.requirement_id
            for item in resolved_report.coverage
            if item.requirement_id in conflict_requirement_ids
            and item.status is not CoverageStatus.SATISFIED
        )
        if unresolved_conflicts:
            raise ValueError(
                "conflicting evidence requires one resolved value for: "
                + ", ".join(unresolved_conflicts)
            )

    work.ctx.catalogue.set_run_status(work.run_id, RunStatus.RUNNING)
    work.event(
        "mapping.review_completed",
        "Applied trusted human decisions to the complete mapping cycle.",
    )
    review_fingerprint = model_fingerprint(
        {
            "cycle": state["mapping_cycle_id"],
            "mapping": result.model_dump(mode="json", by_alias=True),
        }
    )
    snapshot = update_product_snapshot(
        work,
        ProductWorkStage.HUMAN_REVIEW,
        evidence_artifact_id=evidence_id,
        reviewed_mapping_artifact_id=mapping_id,
        review_fingerprint=review_fingerprint,
        reviewed_evidence_fingerprint=state.get("evidence_fingerprint") or state.get("source_fingerprint"),
        reviewed_target_fingerprint=state.get("target_fingerprint"),
        reviewed_mapping_input_fingerprint=state.get("mapping_input_fingerprint"),
        mapping_cycle_id=state["mapping_cycle_id"],
        human_review_pending=False,
        conflicting_requirement_ids=(),
        conflict_artifact_id=None,
    )
    return {
        "evidence_artifact_id": evidence_id,
        "reviewed_mapping_artifact_id": mapping_id,
        "review_required": False,
        "review_fingerprint": review_fingerprint,
        "product_snapshot_version": snapshot.version,
    }


async def coverage(
    state: MiaWorkflowState,
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    work = RunWorkspace(state, runtime.context)
    package = work.load_state("evidence_artifact_id", ProductKnowledgePackage)
    index = work.load_state("targets_artifact_id", TemplateIndex)
    mapping_id = state.get("reviewed_mapping_artifact_id") or work.state_id(
        "semantic_mapping_artifact_id"
    )
    result = work.load(mapping_id, MappingResult)
    report = calculate_coverage(package, index, mapping_result=result)
    artifact_id = work.put_model(
        "mapping/coverage.json",
        report,
        derived_from=(mapping_id,),
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
        work,
        ProductWorkStage.COVERAGE,
        coverage_artifact_id=artifact_id,
        unresolved_required_ids=unresolved,
    )
    return {
        "coverage_artifact_id": artifact_id,
        "required_unresolved": len(unresolved),
        "missing_requirement_ids": unresolved,
        "product_snapshot_version": snapshot.version,
    }


async def integrate_background_research(
    state: MiaWorkflowState,
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    """Adopt completed research artifacts without waiting for the background worker."""

    job_id = state.get("background_job_id")
    if not job_id:
        return {}
    work = RunWorkspace(state, runtime.context)
    job = work.ctx.catalogue.get_background_job(job_id, user_id=work.user_id)
    if job is None or job.status is not BackgroundJobStatus.COMPLETED:
        return {}
    evidence_artifact = job.metadata.get("researchEvidenceArtifactId")
    if not isinstance(evidence_artifact, str):
        return {}
    existing_package = work.load_state("evidence_artifact_id", ProductKnowledgePackage)
    research_package = work.load(evidence_artifact, ProductKnowledgePackage)
    merged_package = merge_packages(existing_package, research_package, preserve_existing=True)
    known_ids = {item.id for item in existing_package.evidence}
    new_ids = {item.id for item in merged_package.evidence} - known_ids
    if not new_ids:
        snapshot = update_product_snapshot(
            work,
            ProductWorkStage.MAPPING,
            last_integrated_research_job_id=job.id,
        )
        return {
            "background_job_id": job.id,
            "product_snapshot_version": snapshot.version,
        }

    current_mapping_id = state.get("reviewed_mapping_artifact_id") or work.state_id(
        "semantic_mapping_artifact_id"
    )
    current_mapping = work.load(current_mapping_id, MappingResult)
    incremental_mapping_id = job.metadata.get("newMappingArtifactId")
    if isinstance(incremental_mapping_id, str):
        incremental_mapping = work.load(incremental_mapping_id, MappingResult)
    else:
        mapper = work.ctx.semantic_mapper
        if mapper is None:
            return {}
        index = work.load_state("targets_artifact_id", TemplateIndex)
        incremental = merged_package.model_copy(
            update={
                "evidence": tuple(item for item in merged_package.evidence if item.id in new_ids)
            }
        )
        deterministic = await DeterministicWebsiteMapper(work.ctx.templates, index).propose(
            incremental.evidence
        )
        semantic = await mapper.map(incremental, index, deterministic)
        incremental_mapping = work.ctx.mapping_review.apply_semantic_run(
            incremental,
            deterministic,
            index,
            semantic,
        )
    conflicts = detect_review_conflicts(
        existing_package,
        research_package,
        current_mapping,
        incremental_mapping,
        index,
    )
    merged_mapping = merge_mapping_results(current_mapping, incremental_mapping)
    merged_mapping = require_review_for_conflicts(merged_mapping, index, conflicts)
    merged_package = mark_conflicting_evidence(merged_package, conflicts)

    merged_evidence_id = work.put_model(
        "evidence/product-knowledge-integrated.json",
        merged_package,
        derived_from=(work.state_id("evidence_artifact_id"), evidence_artifact),
    )
    merged_mapping_id = work.put_model(
        "mapping/research-integrated.json",
        merged_mapping,
        derived_from=(current_mapping_id,),
    )
    conflict_artifact_id = ""
    conflict_requirement_ids: tuple[str, ...] = ()
    review_items_id = ""
    mapping_cycle_id = state.get("mapping_cycle_id", "")
    if conflicts:
        conflict_requirement_ids = tuple(dict.fromkeys(item.requirement_id for item in conflicts))
        conflict_artifact_id = work.put_json(
            "mapping/evidence-conflicts.json",
            [item.model_dump(mode="json", by_alias=True) for item in conflicts],
            derived_from=(merged_evidence_id, merged_mapping_id),
        )
        mapping_cycle_id = work.ctx.mapping_review.cycle_id(
            merged_package,
            index,
            merged_mapping,
        )
        all_reviews = work.ctx.mapping_review.complete_review(
            merged_package,
            merged_mapping,
            index,
        )
        focused_reviews = tuple(
            item for item in all_reviews if item.requirement_id in set(conflict_requirement_ids)
        )
        review_items_id = work.put_json(
            "mapping/review-items-conflicts.json",
            [item.model_dump(mode="json", by_alias=True) for item in focused_reviews],
            derived_from=(merged_mapping_id, conflict_artifact_id),
        )

    work.event(
        "research.integrated",
        (
            f"Integrated {len(new_ids)} new background evidence records; "
            f"{len(conflicts)} reviewed-value conflicts require confirmation."
            if conflicts
            else f"Integrated {len(new_ids)} new background evidence records without replacing review state."
        ),
        metadata={
            "jobId": job.id,
            "evidenceAdded": len(new_ids),
            "conflicts": len(conflicts),
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
        work,
        ProductWorkStage.HUMAN_REVIEW if conflicts else ProductWorkStage.MAPPING,
        evidence_artifact_id=merged_evidence_id,
        reviewed_mapping_artifact_id=(None if conflicts else merged_mapping_id),
        semantic_mapping_artifact_id=merged_mapping_id,
        source_fingerprint=merged_fingerprint,
        evidence_fingerprint=merged_fingerprint,
        mapping_cycle_id=mapping_cycle_id or None,
        human_review_pending=bool(conflicts),
        conflicting_requirement_ids=conflict_requirement_ids,
        conflict_artifact_id=conflict_artifact_id or None,
        last_integrated_research_job_id=job.id,
    )
    update: dict[str, Any] = {
        "evidence_artifact_id": merged_evidence_id,
        "source_fingerprint": merged_fingerprint,
        "evidence_fingerprint": merged_fingerprint,
        "background_job_id": job.id,
        "known_source_urls": tuple(
            dict.fromkeys(
                (
                    *state.get("known_source_urls", ()),
                    *(source.final_url for source in merged_package.acquired_sources),
                )
            )
        ),
    }
    update["semantic_mapping_artifact_id"] = merged_mapping_id
    update["product_snapshot_version"] = snapshot.version
    if conflicts:
        update.update(
            {
                "reviewed_mapping_artifact_id": "",
                "review_items_artifact_id": review_items_id,
                "mapping_cycle_id": mapping_cycle_id,
                "review_required": True,
                "conflict_artifact_id": conflict_artifact_id,
                "conflicting_requirement_ids": conflict_requirement_ids,
            }
        )
    elif state.get("reviewed_mapping_artifact_id"):
        update["reviewed_mapping_artifact_id"] = merged_mapping_id
    return update


async def human_value(
    state: MiaWorkflowState,
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    """Ask for one mandatory value only after public-source research is exhausted."""

    from langgraph.types import interrupt

    work = RunWorkspace(state, runtime.context)
    missing = state.get("missing_requirement_ids", ())
    if not missing:
        return {}
    requirement_id = missing[0]
    index = work.load_state("targets_artifact_id", TemplateIndex)
    requirement = next(item for item in index.requirements if item.id == requirement_id)
    question = (
        f"Provide {requirement.id_short or '/'.join(requirement.template_path)} "
        "because no authoritative public source supplied this mandatory value."
    )
    work.ctx.catalogue.set_run_status(work.run_id, RunStatus.AWAITING_HUMAN)
    payload = interrupt(
        {
            "kind": "requirement_value",
            "threadId": state["thread_id"],
            "productId": work.product_id,
            "requirementId": requirement_id,
            "question": question,
        }
    )
    request = AgentValueRequest.model_validate(payload)
    if request.thread_id != state["thread_id"] or request.product_id != work.product_id:
        raise ValueError("human value does not belong to this workflow")
    if request.requirement_id != requirement_id:
        raise ValueError("human value targets a different requirement")

    package = work.load_state("evidence_artifact_id", ProductKnowledgePackage)
    mapping_id = state.get("reviewed_mapping_artifact_id") or work.state_id(
        "semantic_mapping_artifact_id"
    )
    result = work.load(mapping_id, MappingResult)
    package, result = work.ctx.mapping_review.record_human_value(
        package,
        result,
        index,
        requirement_id=requirement_id,
        value=request.value,
        thread_id=state["thread_id"],
        actor_name=request.actor_name,
        use_dummy=request.use_dummy,
    )
    supplied_mapping = result.mapped[-1]
    supplied_evidence = package.evidence[-1]
    work.ctx.catalogue.add_human_review(
        supplied_value_record(
            user_id=work.user_id,
            product_id=work.product_id,
            run_id=work.run_id,
            thread_id=state["thread_id"],
            requirement_id=requirement_id,
            evidence_id=supplied_evidence.id,
            mapping_id=supplied_mapping.id,
            actor_name=request.actor_name,
            use_dummy=request.use_dummy,
            final_value=_audit_evidence_value(supplied_evidence),
            final_target_path=supplied_mapping.target.template_path,
        )
    )
    evidence_id = work.put_model(
        "evidence/product-knowledge-human.json",
        package,
        derived_from=(work.state_id("evidence_artifact_id"),),
    )
    reviewed_id = work.put_model(
        "mapping/human-value.json",
        result,
        derived_from=(mapping_id,),
    )
    work.ctx.catalogue.set_run_status(work.run_id, RunStatus.RUNNING)
    work.event("human.value_recorded", question, metadata={"requirementId": requirement_id})
    review_fingerprint = model_fingerprint(result)
    snapshot = update_product_snapshot(
        work,
        ProductWorkStage.HUMAN_INPUT,
        evidence_artifact_id=evidence_id,
        reviewed_mapping_artifact_id=reviewed_id,
        review_fingerprint=review_fingerprint,
        reviewed_evidence_fingerprint=state.get("evidence_fingerprint") or state.get("source_fingerprint"),
        reviewed_target_fingerprint=state.get("target_fingerprint"),
        reviewed_mapping_input_fingerprint=state.get("mapping_input_fingerprint"),
        unresolved_required_ids=tuple(item for item in missing if item != requirement_id),
    )
    return {
        "evidence_artifact_id": evidence_id,
        "reviewed_mapping_artifact_id": reviewed_id,
        "review_fingerprint": review_fingerprint,
        "product_snapshot_version": snapshot.version,
    }


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
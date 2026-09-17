"""Template, deterministic mapping, semantic mapping, HITL review, and coverage nodes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

from mia_dpp.aas.requirements import build_template_index
from mia_dpp.agent.models import AgentReviewRequest, AgentValueRequest
from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.mappings import CoverageStatus, MappingResult, MappingStatus, SemanticReviewItem
from mia_dpp.domain.product import RunStatus
from mia_dpp.domain.targets import RequirementKind, TemplateIndex
from mia_dpp.tools.mapping.coverage import coverage as calculate_coverage
from mia_dpp.tools.mapping.mapper import DeterministicWebsiteMapper
from mia_dpp.tools.mapping.models import SemanticMappingRun
from mia_dpp.workflow.context import MiaContext
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
    return {"targets_artifact_id": artifact_id}


async def deterministic_mapping(
    state: MiaWorkflowState,
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    work = RunWorkspace(state, runtime.context)
    package = work.load_state("evidence_artifact_id", ProductKnowledgePackage)
    index = work.load_state("targets_artifact_id", TemplateIndex)
    result = await DeterministicWebsiteMapper(work.ctx.templates, index).propose(package.evidence)
    artifact_id = work.put_model(
        "mapping/deterministic.json",
        result,
        derived_from=(work.state_id("evidence_artifact_id"), work.state_id("targets_artifact_id")),
    )
    return {"deterministic_mapping_artifact_id": artifact_id}


async def semantic_mapping(
    state: MiaWorkflowState,
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    work = RunWorkspace(state, runtime.context)
    mapper = work.ctx.semantic_mapper
    if mapper is None:
        raise RuntimeError("semantic mapping requires a configured model")
    package = work.load_state("evidence_artifact_id", ProductKnowledgePackage)
    index = work.load_state("targets_artifact_id", TemplateIndex)
    deterministic = work.load_state("deterministic_mapping_artifact_id", MappingResult)
    product = work.ctx.catalogue.get_product(work.product_id)
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
        )
    )
    semantic_run = await mapper.map(
        package, index, deterministic, reviewed_knowledge=knowledge
    )
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
        },
    )
    return {
        "semantic_mapping_artifact_id": mapping_id,
        "review_items_artifact_id": review_id,
        "mapping_cycle_id": cycle_id,
        "review_required": bool(reviews),
    }


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
        package, result, item = work.ctx.mapping_review.decide(
            package,
            result,
            index,
            by_id[decision.review_id],
            decision=decision.decision,
            thread_id=state["thread_id"],
            corrected_requirement_id=decision.corrected_requirement_id,
            corrected_value=decision.corrected_value,
            comment=decision.comment,
        )
        reviewed.append(item)
        if item.mapping is not None:
            product = work.ctx.catalogue.get_product(work.product_id)
            domain = (urlsplit(product.canonical_url).hostname or "") if product else None
            work.ctx.catalogue.remember_mapping_review(
                item.mapping,
                decision=decision.decision,
                manufacturer=product.manufacturer if product else None,
                domain=domain,
                product_family=None,
                comment=decision.comment,
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
            "decisions": [item.model_dump(mode="json", by_alias=True) for item in request.decisions],
            "result": [item.model_dump(mode="json", by_alias=True) for item in reviewed],
        },
        derived_from=(mapping_id,),
    )
    work.ctx.catalogue.set_run_status(work.run_id, RunStatus.RUNNING)
    work.event(
        "mapping.review_completed",
        "Applied trusted human decisions to the complete mapping cycle.",
    )
    return {
        "evidence_artifact_id": evidence_id,
        "reviewed_mapping_artifact_id": mapping_id,
        "review_required": False,
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
    return {
        "coverage_artifact_id": artifact_id,
        "required_unresolved": len(unresolved),
        "missing_requirement_ids": unresolved,
    }


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
    return {
        "evidence_artifact_id": evidence_id,
        "reviewed_mapping_artifact_id": reviewed_id,
    }

"""Thin LangGraph adapters for mapping, review interrupts, and research integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

from mia_dpp.agent.models import AgentReviewRequest, AgentValueRequest
from mia_dpp.domain.mappings import SemanticReviewItem
from mia_dpp.domain.product import RunStatus
from mia_dpp.domain.targets import TemplateIndex
from mia_dpp.runtime.run_context import RunContext
from mia_dpp.runtime.services import ServiceContainer
from mia_dpp.services.human_submission import (
    HumanValueSubmission,
    MappingReviewDecision,
    MappingReviewSubmission,
)
from mia_dpp.services.mapping_execution import MappingExecutionService
from mia_dpp.services.mapping_human import MappingHumanService
from mia_dpp.services.research_mapping import (
    ResearchIntegrationRequest,
    ResearchMappingIntegrationService,
)
from mia_dpp.services.semantic_mapping import SemanticMappingService
from mia_dpp.workflow.state import MiaWorkflowState
from mia_dpp.workflow.workspace import RunWorkspace


def _mapping_service(runtime: Runtime[ServiceContainer]) -> MappingExecutionService:
    ctx = runtime.context
    return MappingExecutionService(
        catalogue=ctx.catalogue,
        artifacts=ctx.artifacts,
        templates=ctx.templates,
    )


def _semantic_mapping_service(
    runtime: Runtime[ServiceContainer],
) -> SemanticMappingService:
    ctx = runtime.context
    return SemanticMappingService(
        catalogue=ctx.catalogue,
        artifacts=ctx.artifacts,
        templates=ctx.templates,
        mapping_review=ctx.mapping_review,
        semantic_mapper=ctx.semantic_mapper,
        jev_mapping_enabled=ctx.jev_mapping_enabled,
    )


def _research_mapping_service(
    runtime: Runtime[ServiceContainer],
) -> ResearchMappingIntegrationService:
    ctx = runtime.context
    return ResearchMappingIntegrationService(
        catalogue=ctx.catalogue,
        artifacts=ctx.artifacts,
        templates=ctx.templates,
        mapping_review=ctx.mapping_review,
        semantic_mapper=ctx.semantic_mapper,
        jev_decider=ctx.jev_decider,
        jev_mapping_enabled=ctx.jev_mapping_enabled,
        jev_routing_scopes=ctx.jev_routing_scopes,
        jev_routing_max_concurrency=ctx.jev_routing_max_concurrency,
        jev_decision_policy=ctx.jev_decision_policy,
    )


def _human_service(runtime: Runtime[ServiceContainer]) -> MappingHumanService:
    ctx = runtime.context
    return MappingHumanService(
        catalogue=ctx.catalogue,
        artifacts=ctx.artifacts,
        mapping_review=ctx.mapping_review,
    )


async def build_targets(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    result = _mapping_service(runtime).build_targets(
        RunContext.from_mapping(state),
        evidence_artifact_id=state["evidence_artifact_id"],
        template_keys=state.get(
            "target_submodels",
            ("digital_nameplate", "technical_data"),
        ),
        expected_snapshot_version=int(state.get("product_snapshot_version", 0)),
        source_generation=int(state.get("source_generation", 0)),
    )
    return {
        "targets_artifact_id": result.artifact_id,
        "target_fingerprint": result.target_fingerprint,
        "product_snapshot_version": result.product_snapshot_version,
    }


async def deterministic_mapping(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    result = await _mapping_service(runtime).deterministic_map(
        RunContext.from_mapping(state),
        evidence_artifact_id=state["evidence_artifact_id"],
        targets_artifact_id=state["targets_artifact_id"],
        evidence_fingerprint=state.get("evidence_fingerprint"),
        source_fingerprint=state.get("source_fingerprint"),
        target_fingerprint=state.get("target_fingerprint"),
        expected_snapshot_version=int(state.get("product_snapshot_version", 0)),
        source_generation=int(state.get("source_generation", 0)),
    )
    return {
        "deterministic_mapping_artifact_id": result.artifact_id,
        "mapping_input_fingerprint": result.mapping_input_fingerprint,
        "product_snapshot_version": result.product_snapshot_version,
    }


async def semantic_mapping(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    result = await _semantic_mapping_service(runtime).map(
        RunContext.from_mapping(state),
        evidence_artifact_id=state["evidence_artifact_id"],
        targets_artifact_id=state["targets_artifact_id"],
        deterministic_mapping_artifact_id=state["deterministic_mapping_artifact_id"],
        reviewed_mapping_artifact_id=state.get("reviewed_mapping_artifact_id") or None,
        reuse_prior_work=state.get("reuse_prior_work", False),
        seeded_from_run_id=state.get("seeded_from_run_id") or None,
        evidence_fingerprint=state.get("evidence_fingerprint"),
        source_fingerprint=state.get("source_fingerprint"),
        target_fingerprint=state.get("target_fingerprint"),
        template_keys=state.get(
            "target_submodels",
            ("digital_nameplate", "technical_data"),
        ),
        jev_routing_artifact_id=state.get("jev_idta_routing_artifact_id"),
        jev_policy_artifact_id=state.get("jev_decision_policy_artifact_id"),
        expected_snapshot_version=int(state.get("product_snapshot_version", 0)),
        source_generation=int(state.get("source_generation", 0)),
    )
    updates: dict[str, Any] = {
        "semantic_mapping_artifact_id": result.mapping_artifact_id,
        "review_items_artifact_id": result.review_items_artifact_id,
        "mapping_cycle_id": result.mapping_cycle_id,
        "review_required": result.review_required,
        "product_snapshot_version": result.product_snapshot_version,
    }
    if result.reviewed_mapping_artifact_id is not None:
        updates["reviewed_mapping_artifact_id"] = result.reviewed_mapping_artifact_id
    elif result.clear_reviewed_mapping:
        updates["reviewed_mapping_artifact_id"] = ""
    if result.semantic_mapper_fingerprint is not None:
        updates["semantic_mapper_fingerprint"] = result.semantic_mapper_fingerprint
    return updates


async def human_review(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    """Own the LangGraph pause/resume boundary; delegate decision application."""

    from langgraph.types import interrupt

    work = RunWorkspace(state, runtime.context)
    reviews = tuple(
        SemanticReviewItem.model_validate(item)
        for item in work.load_state_json("review_items_artifact_id")
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
    if {item.review_id for item in request.decisions} != {item.id for item in reviews}:
        raise ValueError("mapping review must contain exactly one decision for every row")

    submission = MappingReviewSubmission(
        actor_name=request.actor_name,
        decisions=tuple(
            MappingReviewDecision(
                review_id=item.review_id,
                decision=item.decision,
                corrected_requirement_id=item.corrected_requirement_id,
                corrected_semantic_id=item.corrected_semantic_id,
                corrected_value=item.corrected_value,
                comment=item.comment,
            )
            for item in request.decisions
        ),
    )
    result = _human_service(runtime).apply_review(
        RunContext.from_mapping(state),
        submission=submission,
        reviews=reviews,
        evidence_artifact_id=state["evidence_artifact_id"],
        targets_artifact_id=state["targets_artifact_id"],
        semantic_mapping_artifact_id=state["semantic_mapping_artifact_id"],
        mapping_cycle_id=state["mapping_cycle_id"],
        conflicting_requirement_ids=state.get("conflicting_requirement_ids", ()),
        evidence_fingerprint=state.get("evidence_fingerprint"),
        source_fingerprint=state.get("source_fingerprint"),
        target_fingerprint=state.get("target_fingerprint"),
        mapping_input_fingerprint=state.get("mapping_input_fingerprint"),
        expected_snapshot_version=int(state.get("product_snapshot_version", 0)),
        source_generation=int(state.get("source_generation", 0)),
    )
    return {
        "evidence_artifact_id": result.evidence_artifact_id,
        "reviewed_mapping_artifact_id": result.reviewed_mapping_artifact_id,
        "review_required": False,
        "review_fingerprint": result.review_fingerprint,
        "product_snapshot_version": result.product_snapshot_version,
    }


async def coverage(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    mapping_id = state.get("reviewed_mapping_artifact_id") or state["semantic_mapping_artifact_id"]
    result = _mapping_service(runtime).coverage(
        RunContext.from_mapping(state),
        evidence_artifact_id=state["evidence_artifact_id"],
        targets_artifact_id=state["targets_artifact_id"],
        mapping_artifact_id=mapping_id,
        expected_snapshot_version=int(state.get("product_snapshot_version", 0)),
        source_generation=int(state.get("source_generation", 0)),
    )
    return {
        "coverage_artifact_id": result.artifact_id,
        "required_unresolved": len(result.unresolved_requirement_ids),
        "missing_requirement_ids": result.unresolved_requirement_ids,
        "product_snapshot_version": result.product_snapshot_version,
    }


async def integrate_background_research(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    job_id = state.get("background_job_id")
    if not job_id:
        return {}

    result = await _research_mapping_service(runtime).integrate(
        ResearchIntegrationRequest(
            context=RunContext.from_mapping(state),
            background_job_id=job_id,
            integrated_iteration=int(state.get("integrated_research_iteration", 0)),
            evidence_artifact_id=state["evidence_artifact_id"],
            targets_artifact_id=state["targets_artifact_id"],
            semantic_mapping_artifact_id=state["semantic_mapping_artifact_id"],
            reviewed_mapping_artifact_id=(state.get("reviewed_mapping_artifact_id") or None),
            review_items_artifact_id=state.get("review_items_artifact_id") or None,
            known_source_urls=state.get("known_source_urls", ()),
            expected_snapshot_version=int(state.get("product_snapshot_version", 0)),
            source_generation=int(state.get("source_generation", 0)),
        )
    )
    if result is None:
        return {}

    updates: dict[str, Any] = {}
    if result.integrated_iteration is not None:
        updates["integrated_research_iteration"] = result.integrated_iteration
    if result.background_job_id is not None:
        updates["background_job_id"] = result.background_job_id
    if result.product_snapshot_version is not None:
        updates["product_snapshot_version"] = result.product_snapshot_version
    if result.evidence_artifact_id is not None:
        updates["evidence_artifact_id"] = result.evidence_artifact_id
    if result.source_fingerprint is not None:
        updates["source_fingerprint"] = result.source_fingerprint
    if result.evidence_fingerprint is not None:
        updates["evidence_fingerprint"] = result.evidence_fingerprint
    if result.known_source_urls is not None:
        updates["known_source_urls"] = result.known_source_urls
    if result.semantic_mapping_artifact_id is not None:
        updates["semantic_mapping_artifact_id"] = result.semantic_mapping_artifact_id
    if result.reviewed_mapping_artifact_id is not None:
        updates["reviewed_mapping_artifact_id"] = result.reviewed_mapping_artifact_id
    elif result.clear_reviewed_mapping:
        updates["reviewed_mapping_artifact_id"] = ""
    if result.review_items_artifact_id is not None:
        updates["review_items_artifact_id"] = result.review_items_artifact_id
    if result.mapping_cycle_id is not None:
        updates["mapping_cycle_id"] = result.mapping_cycle_id
    if result.review_required is not None:
        updates["review_required"] = result.review_required
    if result.conflict_artifact_id is not None:
        updates["conflict_artifact_id"] = result.conflict_artifact_id
    if result.conflicting_requirement_ids:
        updates["conflicting_requirement_ids"] = result.conflicting_requirement_ids
    return updates


async def human_value(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    """Own the LangGraph value-request interrupt; delegate trusted persistence."""

    from langgraph.types import interrupt

    missing = state.get("missing_requirement_ids", ())
    if not missing:
        return {}
    requirement_id = missing[0]
    work = RunWorkspace(state, runtime.context)
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

    mapping_id = state.get("reviewed_mapping_artifact_id") or state["semantic_mapping_artifact_id"]
    submission = HumanValueSubmission(
        value=request.value,
        use_dummy=request.use_dummy,
        actor_name=request.actor_name,
    )
    result = _human_service(runtime).record_human_value(
        RunContext.from_mapping(state),
        submission=submission,
        requirement_id=requirement_id,
        question=question,
        evidence_artifact_id=state["evidence_artifact_id"],
        targets_artifact_id=state["targets_artifact_id"],
        mapping_artifact_id=mapping_id,
        missing_requirement_ids=missing,
        evidence_fingerprint=state.get("evidence_fingerprint"),
        source_fingerprint=state.get("source_fingerprint"),
        target_fingerprint=state.get("target_fingerprint"),
        mapping_input_fingerprint=state.get("mapping_input_fingerprint"),
        expected_snapshot_version=int(state.get("product_snapshot_version", 0)),
        source_generation=int(state.get("source_generation", 0)),
    )
    return {
        "evidence_artifact_id": result.evidence_artifact_id,
        "reviewed_mapping_artifact_id": result.reviewed_mapping_artifact_id,
        "review_fingerprint": result.review_fingerprint,
        "product_snapshot_version": result.product_snapshot_version,
    }

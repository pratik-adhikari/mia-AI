"""Thin LangGraph adapters for reusable semantic preparation capabilities."""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from mia_dpp.runtime.run_context import RunContext
from mia_dpp.runtime.services import ServiceContainer
from mia_dpp.services.semantic_preparation import SemanticPreparationService
from mia_dpp.workflow.state import MiaWorkflowState


def _service(runtime: Runtime[ServiceContainer]) -> SemanticPreparationService:
    ctx = runtime.context
    return SemanticPreparationService(
        catalogue=ctx.catalogue,
        artifacts=ctx.artifacts,
        templates=ctx.templates,
        jev_decider=ctx.jev_decider,
        jev_mapping_enabled=ctx.jev_mapping_enabled,
        jev_routing_scopes=ctx.jev_routing_scopes,
        jev_routing_max_concurrency=ctx.jev_routing_max_concurrency,
        jev_decision_policy=ctx.jev_decision_policy,
        jev_grouping_scopes=ctx.jev_grouping_scopes,
        jev_grouping_max_groups=ctx.jev_grouping_max_groups,
        eclass_shadow_enabled=ctx.eclass_shadow_enabled,
        eclass_provider=ctx.eclass_provider,
        eclass_resolution_scopes=ctx.eclass_resolution_scopes,
        eclass_candidate_limit=ctx.eclass_candidate_limit,
    )


async def normalize_evidence(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    artifact_id = _service(runtime).normalize(
        RunContext.from_mapping(state),
        state["evidence_artifact_id"],
    )
    return {"normalization_artifact_id": artifact_id}


async def build_semantic_context(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    artifact_id = _service(runtime).build_context(
        RunContext.from_mapping(state),
        state["evidence_artifact_id"],
        state["normalization_artifact_id"],
    )
    return {"semantic_context_artifact_id": artifact_id}


async def shadow_jev_idta_routing(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    artifact_id = await _service(runtime).route_jev(
        RunContext.from_mapping(state),
        evidence_artifact_id=state["evidence_artifact_id"],
        normalization_artifact_id=state["normalization_artifact_id"],
        semantic_context_artifact_id=state["semantic_context_artifact_id"],
        template_keys=state.get(
            "target_submodels",
            ("digital_nameplate", "technical_data"),
        ),
    )
    return {"jev_idta_routing_artifact_id": artifact_id} if artifact_id else {}


async def analyze_jev_shadow(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    result = _service(runtime).analyze_jev(
        RunContext.from_mapping(state),
        state.get("jev_idta_routing_artifact_id"),
    )
    if result is None:
        return {}
    diagnostics_id, policy_id = result
    return {
        "jev_routing_diagnostics_artifact_id": diagnostics_id,
        "jev_decision_policy_artifact_id": policy_id,
    }


async def shadow_jev_semantic_grouping(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    artifact_id = await _service(runtime).group_semantics(
        RunContext.from_mapping(state),
        evidence_artifact_id=state["evidence_artifact_id"],
        normalization_artifact_id=state["normalization_artifact_id"],
        semantic_context_artifact_id=state["semantic_context_artifact_id"],
    )
    return {"jev_semantic_grouping_artifact_id": artifact_id}


async def shadow_eclass_resolution(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    artifact_id = await _service(runtime).resolve_eclass(
        RunContext.from_mapping(state),
        evidence_artifact_id=state["evidence_artifact_id"],
        normalization_artifact_id=state["normalization_artifact_id"],
        semantic_context_artifact_id=state["semantic_context_artifact_id"],
        routing_artifact_id=state.get("jev_idta_routing_artifact_id"),
    )
    return {"eclass_resolution_artifact_id": artifact_id} if artifact_id else {}


async def analyze_eclass_shadow(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    result = _service(runtime).analyze_eclass(
        RunContext.from_mapping(state),
        state.get("eclass_resolution_artifact_id"),
    )
    if result is None:
        return {}
    diagnostics_id, policy_id = result
    return {
        "eclass_diagnostics_artifact_id": diagnostics_id,
        "eclass_policy_artifact_id": policy_id,
    }


async def shadow_open_property_proposals(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    artifact_id = _service(runtime).propose_open_properties(
        RunContext.from_mapping(state),
        evidence_artifact_id=state["evidence_artifact_id"],
        normalization_artifact_id=state["normalization_artifact_id"],
        resolution_artifact_id=state.get("eclass_resolution_artifact_id"),
        diagnostics_artifact_id=state.get("eclass_diagnostics_artifact_id"),
    )
    return {"open_property_proposals_artifact_id": artifact_id} if artifact_id else {}

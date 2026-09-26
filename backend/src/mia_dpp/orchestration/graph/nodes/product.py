"""Thin LangGraph adapters for product lifecycle and evidence acquisition."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

from mia_dpp.runtime.run_context import RunContext
from mia_dpp.runtime.services import ServiceContainer
from mia_dpp.services.evidence_acquisition import (
    EvidenceAcquisitionRequest,
    EvidenceAcquisitionService,
)
from mia_dpp.services.product_lifecycle import (
    ProductLifecycleService,
    ProductResolutionRequest,
)
from mia_dpp.orchestration.graph.state import MiaWorkflowState, reset_product_state


async def resolve_product(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    """Resolve durable product/run identity through the reusable lifecycle capability."""

    if state.get("product_id") and state.get("run_id"):
        return {}

    result = ProductLifecycleService(
        runtime.context.catalogue,
        runtime.context.artifacts,
    ).resolve(
        ProductResolutionRequest(
            product_url=state["product_url"],
            user_id=state["user_id"],
            thread_id=state["thread_id"],
            refresh_requested=state.get("refresh_requested", False),
            max_research_attempts=state.get("max_research_attempts", 2),
        )
    )
    updates: dict[str, Any] = {
        "product_id": result.product_id,
        "run_id": result.run_id,
        "cache_hit": result.cache_hit,
        "reuse_mode": result.reuse_mode.value,
        "reuse_prior_work": result.reuse_prior_work,
        "seeded_from_run_id": result.seeded_from_run_id or "",
        "evidence_artifact_id": result.evidence_artifact_id or "",
        "reviewed_mapping_artifact_id": result.reviewed_mapping_artifact_id or "",
        "background_job_id": result.background_job_id or "",
        "reused_dpp_version_id": result.reused_dpp_version_id or "",
        "product_name": result.product_name or "",
        "manufacturer": result.manufacturer or "",
        "image_url": result.image_url or "",
        "product_snapshot_version": result.product_snapshot_version,
        "workflow_generation": result.workflow_generation,
        "source_generation": result.source_generation,
        "status": result.status,
    }
    if result.max_research_attempts is not None:
        updates["max_research_attempts"] = result.max_research_attempts
    return updates


async def reuse_existing_dpp(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    """Return an already-published DPP through the reusable lifecycle capability."""

    result = ProductLifecycleService(
        runtime.context.catalogue,
        runtime.context.artifacts,
    ).reuse_completed_dpp(RunContext.from_mapping(state))
    return {
        "dpp_artifact_id": result.dpp_artifact_id,
        "aas_artifact_id": result.aas_artifact_id or "",
        "validation_artifact_id": result.validation_artifact_id or "",
        "source_fingerprint": result.source_fingerprint or "",
        "reused_dpp_version_id": result.dpp_version_id,
        "status": result.status,
        "reply": "Existing DPP reused; no extraction or mapping was repeated.",
        "decision_summary": ("A successful durable DPP already exists for this product URL."),
    }


async def extract_evidence(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    """Acquire or reuse product evidence through the reusable acquisition capability."""

    result = await EvidenceAcquisitionService(
        catalogue=runtime.context.catalogue,
        artifacts=runtime.context.artifacts,
        web_tool=runtime.context.web_tool,
    ).acquire(
        EvidenceAcquisitionRequest(
            context=RunContext.from_mapping(state),
            product_url=state["product_url"],
            reuse_prior_work=state.get("reuse_prior_work", False),
            prior_evidence_artifact_id=state.get("evidence_artifact_id") or None,
            refresh_requested=state.get("refresh_requested", False),
            seeded_from_run_id=state.get("seeded_from_run_id") or None,
            reviewed_mapping_artifact_id=state.get("reviewed_mapping_artifact_id") or None,
            target_submodels=state.get("target_submodels", ()),
            known_source_urls=state.get("known_source_urls", ()),
            source_generation=int(state.get("source_generation", 1)),
            product_snapshot_version=int(state.get("product_snapshot_version", 0)),
        )
    )
    updates: dict[str, Any] = {
        "evidence_artifact_id": result.evidence_artifact_id,
        "product_name": result.product_name,
        "known_source_urls": result.known_source_urls,
        "source_fingerprint": result.source_fingerprint,
        "evidence_fingerprint": result.evidence_fingerprint,
        "product_snapshot_version": result.product_snapshot_version,
    }
    if result.manufacturer is not None:
        updates["manufacturer"] = result.manufacturer
    if result.image_url is not None:
        updates["image_url"] = result.image_url
    if result.background_job_id is not None:
        updates["background_job_id"] = result.background_job_id
    return updates


def advance_product(state: MiaWorkflowState) -> dict[str, Any]:
    """Reset run-scoped fields and move to the next selected product URL."""

    queue = state.get("product_queue", ())
    if not queue:
        return {}
    return reset_product_state(product_url=queue[0], product_queue=queue[1:])

"""Thin LangGraph adapters for deterministic AAS build and DPP release."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

from mia_dpp.domain.product import DppReleaseStatus
from mia_dpp.runtime.run_context import RunContext
from mia_dpp.runtime.services import ServiceContainer
from mia_dpp.services.aas_output import AasOutputService
from mia_dpp.orchestration.graph.state import MiaWorkflowState


def _service(runtime: Runtime[ServiceContainer]) -> AasOutputService:
    return AasOutputService(
        catalogue=runtime.context.catalogue,
        artifacts=runtime.context.artifacts,
        templates=runtime.context.templates,
    )


async def build_aas(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    mapping_id = state.get("reviewed_mapping_artifact_id") or state["semantic_mapping_artifact_id"]
    result = _service(runtime).build(
        RunContext.from_mapping(state),
        evidence_artifact_id=state["evidence_artifact_id"],
        mapping_artifact_id=mapping_id,
        coverage_artifact_id=state["coverage_artifact_id"],
        expected_snapshot_version=int(state.get("product_snapshot_version", 0)),
        source_generation=int(state.get("source_generation", 0)),
    )
    return {
        "dpp_artifact_id": result.dpp_artifact_id,
        "aas_artifact_id": result.aas_artifact_id,
        "validation_artifact_id": result.validation_artifact_id,
        "build_deployable": result.deployable,
        "build_input_fingerprint": result.build_input_fingerprint,
        "product_snapshot_version": result.product_snapshot_version,
    }


async def store_result(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    mapping_id = state.get("reviewed_mapping_artifact_id") or state["semantic_mapping_artifact_id"]
    result = _service(runtime).store_result(
        RunContext.from_mapping(state),
        deployable=state.get("build_deployable", False),
        mapping_artifact_id=mapping_id,
        dpp_artifact_id=state["dpp_artifact_id"],
        aas_artifact_id=state.get("aas_artifact_id") or None,
        validation_artifact_id=state.get("validation_artifact_id") or None,
        source_fingerprint=state.get("source_fingerprint") or None,
        required_unresolved=int(state.get("required_unresolved", 0)),
        research_attempts=int(state.get("research_attempts", 0)),
        expected_snapshot_version=int(state.get("product_snapshot_version", 0)),
        source_generation=int(state.get("source_generation", 0)),
    )
    if result.validation_failed:
        return {
            "status": result.status,
            "reply": ("The AAS was built but deterministic validation still blocks deployment."),
            "decision_summary": "Validation failed; no DPP version was published.",
        }

    provisional = result.release_status is DppReleaseStatus.PROVISIONAL
    updates: dict[str, Any] = {
        "status": result.status,
        "reply": (
            "DPP creation completed as a provisional version because human-approved DUMMY "
            "placeholders remain."
            if provisional
            else "DPP creation completed and a verified durable version was stored."
        ),
        "decision_summary": (
            f"Coverage and validation passed, with {result.dummy_mapping_count} DUMMY mapping(s)."
            if provisional
            else "Coverage and validation passed with verified values."
        ),
    }
    if result.dpp_version_id is not None:
        updates["reused_dpp_version_id"] = result.dpp_version_id
    return updates

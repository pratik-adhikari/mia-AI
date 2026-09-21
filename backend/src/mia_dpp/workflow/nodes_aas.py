"""Deterministic AAS build, validation artifact storage, and DPP versioning."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

from mia_dpp.aas.build import build_dpp
from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.mappings import MappingResult, MappingStatus
from mia_dpp.domain.product import RunStatus
from mia_dpp.workflow.context import MiaContext
from mia_dpp.workflow.state import MiaWorkflowState
from mia_dpp.workflow.workspace import RunWorkspace


async def build_aas(
    state: MiaWorkflowState,
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    work = RunWorkspace(state, runtime.context)
    package_input = work.load_state("evidence_artifact_id", ProductKnowledgePackage)
    mapping_id = state.get("reviewed_mapping_artifact_id") or work.state_id(
        "semantic_mapping_artifact_id"
    )
    mapping = work.load(mapping_id, MappingResult)
    accepted = [
        item
        for item in mapping.mapped
        if item.status in {MappingStatus.AUTO, MappingStatus.APPROVED}
    ]
    package = build_dpp(
        package_input.product_name,
        accepted,
        repository=work.ctx.templates,
        evidence=package_input.evidence,
    )
    dpp_id = work.put_model(
        "dpp/package.json",
        package,
        derived_from=(mapping_id, work.state_id("coverage_artifact_id")),
    )
    aas_id = work.put_json(
        "aas/environment.json",
        package.environment,
        derived_from=(dpp_id,),
    )
    validation_id = work.put_model(
        "aas/validation.json",
        package.validation_report,
        derived_from=(aas_id,),
    )
    work.event(
        "aas.validation_completed",
        "Built and deterministically validated the AAS artifact.",
        metadata={"deployable": package.deployable, "artifactSha256": package.artifact_sha256},
    )
    return {
        "dpp_artifact_id": dpp_id,
        "aas_artifact_id": aas_id,
        "validation_artifact_id": validation_id,
        "build_deployable": package.deployable,
    }


async def store_result(
    state: MiaWorkflowState,
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    work = RunWorkspace(state, runtime.context)
    deployable = state.get("build_deployable", False)
    work.ctx.catalogue.finish_run(
        work.run_id,
        RunStatus.COMPLETED if deployable else RunStatus.FAILED,
        metrics={
            "requiredUnresolved": state.get("required_unresolved", 0),
            "researchAttempts": state.get("research_attempts", 0),
        },
        error=None if deployable else "AAS validation did not produce a deployable artifact",
    )
    if not deployable:
        work.event(
            "dpp.validation_failed",
            "Stored failed build/validation artifacts without publishing a DPP version.",
        )
        return {
            "status": "failed",
            "reply": "The AAS was built but deterministic validation still blocks deployment.",
            "decision_summary": "Validation failed; no DPP version was published.",
        }

    version = work.ctx.catalogue.create_dpp_version(
        work.product_id,
        work.run_id,
        dpp_artifact_id=work.state_id("dpp_artifact_id"),
        aas_artifact_id=state.get("aas_artifact_id") or None,
        validation_artifact_id=state.get("validation_artifact_id") or None,
        source_fingerprint=state.get("source_fingerprint") or None,
        deployable=True,
    )
    product = work.ctx.catalogue.get_product(work.product_id, user_id=work.user_id)
    if product is not None:
        from mia_dpp.domain.base import utc_now

        work.ctx.catalogue.update_product(
            product.model_copy(update={"last_verified_at": utc_now()})
        )
    work.event(
        "dpp.version_created",
        f"Stored DPP version {version.version}.",
        metadata={"dppVersionId": version.id},
    )
    return {
        "status": "completed",
        "reused_dpp_version_id": version.id,
        "reply": "DPP creation completed and a durable version was stored.",
        "decision_summary": "Coverage and validation passed.",
    }

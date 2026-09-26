"""Deterministic AAS build, validation artifact storage, and DPP versioning."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

from mia_dpp.capabilities.aas import build_validated_dpp
from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.mappings import MappingResult, MappingStatus
from mia_dpp.domain.product import DppReleaseStatus, RunStatus
from mia_dpp.domain.product_work import ProductWorkStage
from mia_dpp.workflow.context import MiaContext
from mia_dpp.workflow.product_snapshot import model_fingerprint, update_product_snapshot
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
    package = build_validated_dpp(
        package_input,
        mapping,
        templates=work.ctx.templates,
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
    build_input_fingerprint = model_fingerprint(
        {
            "evidence": package_input.model_dump(mode="json", by_alias=True),
            "mapping": mapping.model_dump(mode="json", by_alias=True),
        }
    )
    snapshot = update_product_snapshot(
        work,
        ProductWorkStage.VALIDATION,
        dpp_artifact_id=dpp_id,
        aas_artifact_id=aas_id,
        validation_artifact_id=validation_id,
        build_input_fingerprint=build_input_fingerprint,
        last_error=None
        if package.deployable
        else "AAS validation did not produce a deployable artifact",
    )
    return {
        "dpp_artifact_id": dpp_id,
        "aas_artifact_id": aas_id,
        "validation_artifact_id": validation_id,
        "build_deployable": package.deployable,
        "build_input_fingerprint": build_input_fingerprint,
        "product_snapshot_version": snapshot.version,
    }


async def store_result(
    state: MiaWorkflowState,
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    work = RunWorkspace(state, runtime.context)
    deployable = state.get("build_deployable", False)
    mapping_id = state.get("reviewed_mapping_artifact_id") or work.state_id(
        "semantic_mapping_artifact_id"
    )
    mapping = work.load(mapping_id, MappingResult)
    dummy_mapping_ids = tuple(
        item.id
        for item in mapping.mapped
        if item.status in {MappingStatus.AUTO, MappingStatus.APPROVED}
        and item.human_value_kind == "dummy"
    )
    release_status = (
        DppReleaseStatus.PROVISIONAL if dummy_mapping_ids else DppReleaseStatus.VERIFIED
    )
    metrics: dict[str, int | float | str | bool | None] = {
        "requiredUnresolved": state.get("required_unresolved", 0),
        "researchAttempts": state.get("research_attempts", 0),
    }
    if not deployable:
        update_product_snapshot(
            work,
            ProductWorkStage.FAILED,
            last_error="AAS validation did not produce a deployable artifact",
        )
        work.event(
            "dpp.validation_failed",
            "Stored failed build/validation artifacts without publishing a DPP version.",
        )
        work.ctx.catalogue.finish_run(
            work.run_id,
            RunStatus.FAILED,
            metrics=metrics,
            error="AAS validation did not produce a deployable artifact",
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
        release_status=release_status,
        dummy_mapping_ids=dummy_mapping_ids,
    )
    product = work.ctx.catalogue.get_product(work.product_id, user_id=work.user_id)
    if product is not None and release_status is DppReleaseStatus.VERIFIED:
        from mia_dpp.domain.base import utc_now

        work.ctx.catalogue.update_product(
            product.model_copy(update={"last_verified_at": utc_now()})
        )
    update_product_snapshot(
        work,
        ProductWorkStage.COMPLETED,
        unresolved_required_ids=(),
        human_review_pending=False,
        last_error=None,
        release_status=release_status,
        dummy_mapping_ids=dummy_mapping_ids,
    )
    work.event(
        "dpp.version_created",
        f"Stored DPP version {version.version}.",
        metadata={"dppVersionId": version.id},
    )
    work.ctx.catalogue.finish_run(
        work.run_id,
        RunStatus.COMPLETED,
        metrics=metrics,
    )
    provisional = release_status is DppReleaseStatus.PROVISIONAL
    return {
        "status": "completed",
        "reused_dpp_version_id": version.id,
        "reply": (
            "DPP creation completed as a provisional version because human-approved DUMMY "
            "placeholders remain."
            if provisional
            else "DPP creation completed and a verified durable version was stored."
        ),
        "decision_summary": (
            f"Coverage and validation passed, with {len(dummy_mapping_ids)} DUMMY mapping(s)."
            if provisional
            else "Coverage and validation passed with verified values."
        ),
    }

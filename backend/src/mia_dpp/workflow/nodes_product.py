"""Product identity, cache reuse, and evidence extraction nodes."""

from __future__ import annotations

import hashlib
import mimetypes
import re
from time import perf_counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

from mia_dpp.canonical import sha256_json
from mia_dpp.domain.evidence import ExtractedAsset, ProductKnowledgePackage
from mia_dpp.domain.product import RunStatus
from mia_dpp.domain.product_work import ProductWorkSnapshot, ProductWorkStage, ReuseMode
from mia_dpp.services.product_reuse import ProductReuseService
from mia_dpp.workflow.context import MiaContext
from mia_dpp.services.product_identifiers import discover_product_identifiers
from mia_dpp.persistence.catalogue import ActiveProductRunExists, ProductIdentifierConflict
from mia_dpp.workflow.presentation import evidence_text, product_image_url
from mia_dpp.workflow.product_snapshot import update_product_snapshot
from mia_dpp.workflow.state import MiaWorkflowState, reset_product_state
from mia_dpp.workflow.workspace import RunWorkspace

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


async def resolve_product(
    state: MiaWorkflowState,
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    """Resolve durable identity and reuse a completed DPP unless refresh was requested."""

    if state.get("product_id") and state.get("run_id"):
        return {}
    catalogue = runtime.context.catalogue
    product, _ = catalogue.get_or_create_product(
        state["product_url"],
        user_id=state["user_id"],
    )
    active = catalogue.latest_active_run(product.id, user_id=state["user_id"])
    if active is not None:
        if active.thread_id != state["thread_id"]:
            raise ActiveProductRunExists(active)
        snapshot = catalogue.get_product_work_snapshot(product.id, user_id=state["user_id"])
        return {
            "product_id": product.id,
            "run_id": active.id,
            "reuse_mode": ReuseMode.RESUME_CHECKPOINT.value,
            "reuse_prior_work": True,
            "seeded_from_run_id": active.id,
            "evidence_artifact_id": (
                snapshot.evidence_artifact_id if snapshot and snapshot.evidence_artifact_id else ""
            ),
            "reviewed_mapping_artifact_id": (
                snapshot.reviewed_mapping_artifact_id
                if snapshot and snapshot.reviewed_mapping_artifact_id
                else ""
            ),
            "product_name": product.name or "",
            "manufacturer": product.manufacturer or "",
            "image_url": product.image_url or "",
            "product_snapshot_version": snapshot.version if snapshot is not None else 0,
            "status": (
                "awaiting_human"
                if active.status is RunStatus.AWAITING_HUMAN
                else "running"
            ),
        }

    refresh_requested = state.get("refresh_requested", False)
    durable_snapshot = catalogue.get_product_work_snapshot(
        product.id,
        user_id=state["user_id"],
    )
    decision = ProductReuseService(
        catalogue,
        runtime.context.artifacts,
    ).decide(
        product.id,
        user_id=state["user_id"],
        refresh_requested=refresh_requested,
    )
    cached = (
        catalogue.latest_successful_dpp(product.id, user_id=state["user_id"])
        if decision.mode is ReuseMode.REUSE_COMPLETED_DPP
        else None
    )
    run = catalogue.start_run(
        product.id,
        state["thread_id"],
        user_id=state["user_id"],
        refresh_requested=refresh_requested,
        reused_from_run_id=cached.run_id if cached else None,
        seeded_from_run_id=decision.seeded_from_run_id,
    )
    catalogue.add_event(
        run.id,
        "product.resolved",
        "Resolved product identity and checked the durable DPP cache.",
        metadata={
            "cacheHit": cached is not None,
            "reuseMode": decision.mode.value,
            "reusedPriorWork": decision.mode is ReuseMode.CONTINUE_SAVED_WORK,
            "seededFromRunId": decision.seeded_from_run_id,
            "canonicalUrl": product.canonical_url,
        },
    )
    return {
        "product_id": product.id,
        "run_id": run.id,
        "cache_hit": cached is not None,
        "reuse_mode": decision.mode.value,
        "reuse_prior_work": decision.mode is ReuseMode.CONTINUE_SAVED_WORK,
        "seeded_from_run_id": decision.seeded_from_run_id or "",
        "evidence_artifact_id": decision.evidence_artifact_id or "",
        "reviewed_mapping_artifact_id": decision.reviewed_mapping_artifact_id or "",
        "background_job_id": decision.pending_research_job_id or "",
        "reused_dpp_version_id": decision.reused_dpp_version_id or "",
        "product_name": product.name or "",
        "manufacturer": product.manufacturer or "",
        "image_url": product.image_url or "",
        "product_snapshot_version": (
            durable_snapshot.version if durable_snapshot is not None else 0
        ),
        "status": "reused" if cached else "running",
        "max_research_attempts": state.get("max_research_attempts", 2),
    }


async def reuse_existing_dpp(
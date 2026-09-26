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
from mia_dpp.capabilities.evidence import merge_packages
from mia_dpp.domain.evidence import ExtractedAsset, ProductKnowledgePackage
from mia_dpp.domain.product import RunStatus
from mia_dpp.domain.product_work import ProductWorkStage, ReuseMode
from mia_dpp.persistence.catalogue import ActiveProductRunExists, ProductIdentifierConflict
from mia_dpp.runtime.services import ServiceContainer
from mia_dpp.services.product_identifiers import discover_product_identifiers
from mia_dpp.services.product_reuse import ProductReuseService
from mia_dpp.workflow.presentation import evidence_text, product_image_url
from mia_dpp.workflow.product_snapshot import update_product_snapshot
from mia_dpp.workflow.state import MiaWorkflowState, reset_product_state
from mia_dpp.workflow.workspace import RunWorkspace

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


async def resolve_product(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
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
            "workflow_generation": active.workflow_generation,
            "source_generation": snapshot.source_generation if snapshot is not None else 0,
            "status": (
                "awaiting_human" if active.status is RunStatus.AWAITING_HUMAN else "running"
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
    base_source_generation = (
        durable_snapshot.source_generation if durable_snapshot is not None else 0
    )
    source_generation = (
        base_source_generation + 1
        if decision.mode in {ReuseMode.FRESH, ReuseMode.REFRESH_SOURCES}
        else base_source_generation
    )
    if source_generation == 0:
        source_generation = 1
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
        "workflow_generation": run.workflow_generation,
        "source_generation": source_generation,
        "status": "reused" if cached else "running",
        "max_research_attempts": state.get("max_research_attempts", 2),
    }


async def reuse_existing_dpp(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    catalogue = runtime.context.catalogue
    version = catalogue.latest_successful_dpp(
        state["product_id"],
        user_id=state["user_id"],
    )
    if version is None:
        raise RuntimeError("cache route selected without a successful DPP")
    catalogue.finish_run(state["run_id"], RunStatus.REUSED)
    catalogue.add_event(
        state["run_id"],
        "dpp.reused",
        "Returned the existing successful DPP without repeating extraction or mapping.",
        metadata={"dppVersionId": version.id, "version": version.version},
    )
    return {
        "dpp_artifact_id": version.dpp_artifact_id,
        "aas_artifact_id": version.aas_artifact_id or "",
        "validation_artifact_id": version.validation_artifact_id or "",
        "source_fingerprint": version.source_fingerprint or "",
        "reused_dpp_version_id": version.id,
        "status": "reused",
        "reply": "Existing DPP reused; no extraction or mapping was repeated.",
        "decision_summary": "A successful durable DPP already exists for this product URL.",
    }


async def extract_evidence(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    work = RunWorkspace(state, runtime.context)
    if (
        state.get("reuse_prior_work")
        and state.get("evidence_artifact_id")
        and not state.get("refresh_requested", False)
    ):
        prior_id = work.state_id("evidence_artifact_id")
        package = work.load(prior_id, ProductKnowledgePackage)
        evidence_id = work.put_model(
            "evidence/product-knowledge.json",
            package,
            derived_from=(prior_id,),
        )
        source_urls = tuple(dict.fromkeys(item.final_url for item in package.acquired_sources))
        fingerprint = sha256_json(
            {
                "sources": [item.content_sha256 for item in package.acquired_sources],
                "evidence": [item.id for item in package.evidence],
            }
        )
        work.event(
            "product.work_reused",
            f"Reused {len(package.evidence)} persisted evidence records without crawling again.",
            metadata={
                "seededFromRunId": state.get("seeded_from_run_id"),
                "priorEvidenceArtifactId": prior_id,
                "evidenceArtifactId": evidence_id,
            },
        )
        snapshot = update_product_snapshot(
            work,
            ProductWorkStage.EVIDENCE,
            template_keys=state.get("target_submodels", ()),
            evidence_artifact_id=evidence_id,
            reviewed_mapping_artifact_id=state.get("reviewed_mapping_artifact_id") or None,
            source_fingerprint=fingerprint,
            evidence_fingerprint=fingerprint,
        )
        return {
            "evidence_artifact_id": evidence_id,
            "known_source_urls": source_urls,
            "product_name": package.product_name,
            "source_fingerprint": fingerprint,
            "evidence_fingerprint": fingerprint,
            "product_snapshot_version": snapshot.version,
        }

    total_started = perf_counter()
    crawl_started = perf_counter()
    work.event(
        "crawl.seed.started",
        "Loading and extracting the product page before mapping begins.",
        metadata={"url": state["product_url"], "activityKey": "seed-crawl"},
    )
    incoming = await work.ctx.web_tool.extract(state["product_url"])
    crawl_duration_ms = round((perf_counter() - crawl_started) * 1000, 2)
    incoming = incoming.model_copy(update={"product_id": work.product_id})
    prior_artifact_id = state.get("evidence_artifact_id")
    package = (
        incoming
        if not prior_artifact_id
        else merge_packages(work.load(prior_artifact_id, ProductKnowledgePackage), incoming)
    )

    persist_started = perf_counter()
    raw_ids = tuple(
        work.put_bytes(
            f"sources/{source.id}.html",
            source.rendered_html.encode(),
            content_type="text/html; charset=utf-8",
            derived_from=(prior_artifact_id,) if prior_artifact_id else (),
        )
        for source in incoming.acquired_sources
    )
    markdown_ids = tuple(
        work.put_bytes(
            f"extraction/seed/{source.id}/markdown.md",
            source.markdown.encode(),
            content_type="text/markdown; charset=utf-8",
            derived_from=raw_ids,
        )
        for source in incoming.acquired_sources
        if source.markdown
    )
    structured_ids = tuple(
        work.put_model(
            f"extraction/structured-page-{index}.json",
            page,
            derived_from=raw_ids,
        )
        for index, page in enumerate(incoming.extracted_pages, start=1)
    )

    # Keep the browser-facing seed pass intentionally shallow. Crawl4AI already observed these
    # assets while rendering the page, so persist their provenance now and let durable background
    # research decide which expensive binary downloads are worth acquiring later.
    source_assets = tuple(asset for page in incoming.extracted_pages for asset in page.assets)
    asset_manifest_id = (
        work.put_json(
            "extraction/assets.json",
            [
                {
                    **asset.model_dump(mode="json", by_alias=True),
                    "acquisition": "deferred",
                }
                for asset in source_assets
            ],
            derived_from=raw_ids,
        )
        if source_assets
        else None
    )
    evidence_id = work.put_model(
        "evidence/product-knowledge.json",
        package,
        derived_from=tuple(
            item
            for item in (
                prior_artifact_id,
                *raw_ids,
                *markdown_ids,
                *structured_ids,
                asset_manifest_id,
            )
            if item
        ),
    )
    persistence_duration_ms = round((perf_counter() - persist_started) * 1000, 2)
    work.event(
        "crawl.seed.completed",
        "Completed the shallow seed crawl and persisted evidence without blocking on assets.",
        metadata={
            "durationMs": crawl_duration_ms,
            "sourceCount": len(incoming.acquired_sources),
            "evidenceCount": len(incoming.evidence),
            "deferredAssetCount": len(source_assets),
            "activityKey": "seed-crawl",
        },
    )

    source_urls = tuple(
        dict.fromkeys(
            (
                *state.get("known_source_urls", ()),
                *(item.final_url for item in package.acquired_sources),
            )
        )
    )
    image_url = product_image_url(package)
    manufacturer = evidence_text(package, "manufacturer", "manufacturer name", "brand")
    product = work.ctx.catalogue.get_product(work.product_id, user_id=work.user_id)
    if product is None:
        raise KeyError(work.product_id)
    identifiers = discover_product_identifiers(
        package,
        manufacturer=manufacturer or product.manufacturer,
    )
    possible_duplicate_ids: list[str] = []
    for identifier in identifiers:
        try:
            work.ctx.catalogue.register_product_identifier(
                identifier,
                user_id=work.user_id,
                run_id=work.run_id,
            )
        except ProductIdentifierConflict as error:
            possible_duplicate_ids.append(error.existing_product_id)
    manufacturer_product_id = next(
        (
            item.value
            for item in identifiers
            if item.scheme in {"manufacturer_part_number", "manufacturer_article_number"}
            and item.role.value == "identity"
        ),
        None,
    )
    work.ctx.catalogue.update_product(
        product.model_copy(
            update={
                "name": package.product_name,
                "manufacturer": manufacturer or product.manufacturer,
                "manufacturer_product_id": (
                    product.manufacturer_product_id or manufacturer_product_id
                ),
                "image_url": image_url or product.image_url,
            }
        ),
        run_id=work.run_id,
    )
    if possible_duplicate_ids:
        work.event(
            "product.identity_match_detected",
            "A strong product identity already belongs to another durable product record.",
            metadata={
                "possibleDuplicateProductIds": list(dict.fromkeys(possible_duplicate_ids)),
                "automaticMerge": False,
            },
        )
    fingerprint = sha256_json(
        {
            "sources": [item.content_sha256 for item in package.acquired_sources],
            "evidence": [item.id for item in package.evidence],
        }
    )
    work.event(
        "source.extracted",
        f"Persisted {len(package.evidence)} evidence records.",
        metadata={
            "artifactId": evidence_id,
            "sourceCount": len(source_urls),
            "evidenceCount": len(package.evidence),
            "persistenceDurationMs": persistence_duration_ms,
            "totalDurationMs": round((perf_counter() - total_started) * 1000, 2),
        },
    )
    job = work.ctx.catalogue.create_background_job(
        user_id=work.user_id,
        thread_id=state["thread_id"],
        product_id=work.product_id,
        run_id=work.run_id,
        metadata={
            "seedUrl": state["product_url"],
            "productName": package.product_name,
            "seedEvidenceArtifactId": evidence_id,
            "processedSources": 0,
            "totalSources": 0,
            "iteration": 0,
            "nextSourceIndex": 0,
            "phase": "queued",
            "sourceGeneration": int(state.get("source_generation", 1)),
        },
    )
    job_artifact_id = work.put_model(
        "background/deep-crawl-job.json",
        job,
        derived_from=(evidence_id,),
    )
    work.event(
        "research.queued",
        "Queued durable deep research without blocking initial mapping.",
        metadata={"jobId": job.id, "artifactId": job_artifact_id},
    )
    snapshot = update_product_snapshot(
        work,
        ProductWorkStage.EVIDENCE,
        template_keys=state.get("target_submodels", ()),
        evidence_artifact_id=evidence_id,
        source_fingerprint=fingerprint,
        evidence_fingerprint=fingerprint,
    )
    return {
        "evidence_artifact_id": evidence_id,
        "product_name": package.product_name,
        "manufacturer": manufacturer or "",
        "image_url": image_url or "",
        "known_source_urls": source_urls,
        "source_fingerprint": fingerprint,
        "evidence_fingerprint": fingerprint,
        "background_job_id": job.id,
        "product_snapshot_version": snapshot.version,
    }


def asset_workspace_path(
    asset: ExtractedAsset,
    media_type: str,
) -> str:
    """Build a readable, collision-resistant path under images/ or documents/."""

    suffix = mimetypes.guess_extension(media_type) or {
        "image/webp": ".webp",
        "model/step": ".step",
    }.get(media_type, ".bin")
    stem = _SAFE_FILENAME.sub("-", asset.label).strip("-.").casefold() or asset.kind
    # URL-derived labels can already contain the MIME suffix; avoid names like file.pdf.pdf.
    if stem.endswith(suffix.casefold()):
        stem = stem[: -len(suffix)]
    identifier = hashlib.sha256(asset.url.encode()).hexdigest()[:8]
    if asset.kind == "image":
        return f"images/{stem}-{identifier}{suffix}"
    return f"documents/{stem}-{identifier}{suffix}"




def advance_product(state: MiaWorkflowState) -> dict[str, Any]:
    """Reset run-scoped fields and move to the next selected product URL."""

    queue = state.get("product_queue", ())
    if not queue:
        return {}
    return reset_product_state(product_url=queue[0], product_queue=queue[1:])

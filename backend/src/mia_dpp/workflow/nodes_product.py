"""Product identity, cache reuse, and evidence extraction nodes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

from mia_dpp.canonical import sha256_json
from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.product import RunStatus
from mia_dpp.workflow.context import MiaContext
from mia_dpp.workflow.presentation import evidence_text, product_image_url
from mia_dpp.workflow.state import MiaWorkflowState, reset_product_state
from mia_dpp.workflow.workspace import RunWorkspace


async def resolve_product(
    state: MiaWorkflowState,
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    """Resolve durable identity and reuse a completed DPP unless refresh was requested."""

    if state.get("product_id") and state.get("run_id"):
        return {}
    catalogue = runtime.context.catalogue
    product, _ = catalogue.get_or_create_product(state["product_url"])
    cached = (
        None
        if state.get("refresh_requested", False)
        else catalogue.latest_successful_dpp(product.id)
    )
    run = catalogue.start_run(
        product.id,
        state["thread_id"],
        refresh_requested=state.get("refresh_requested", False),
        reused_from_run_id=cached.run_id if cached else None,
    )
    catalogue.add_event(
        run.id,
        "product.resolved",
        "Resolved product identity and checked the durable DPP cache.",
        metadata={"cacheHit": cached is not None, "canonicalUrl": product.canonical_url},
    )
    return {
        "product_id": product.id,
        "run_id": run.id,
        "cache_hit": cached is not None,
        "reused_dpp_version_id": cached.id if cached else "",
        "product_name": product.name or "",
        "manufacturer": product.manufacturer or "",
        "image_url": product.image_url or "",
        "status": "reused" if cached else "running",
        "max_research_attempts": state.get("max_research_attempts", 2),
    }


async def reuse_existing_dpp(
    state: MiaWorkflowState,
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    catalogue = runtime.context.catalogue
    version = catalogue.latest_successful_dpp(state["product_id"])
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
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    work = RunWorkspace(state, runtime.context)
    incoming = await work.ctx.web_tool.extract(state["product_url"])
    incoming = incoming.model_copy(update={"product_id": work.product_id})
    prior_id = state.get("evidence_artifact_id")
    package = (
        incoming
        if not prior_id
        else _merge_packages(work.load(prior_id, ProductKnowledgePackage), incoming)
    )

    raw_ids = tuple(
        work.put_bytes(
            f"sources/{source.id}.html",
            source.rendered_html.encode(),
            content_type="text/html; charset=utf-8",
            derived_from=(prior_id,) if prior_id else (),
        )
        for source in incoming.acquired_sources
    )
    evidence_id = work.put_model(
        "evidence/product-knowledge.json",
        package,
        derived_from=tuple(item for item in (prior_id, *raw_ids) if item),
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
    product = work.ctx.catalogue.get_product(work.product_id)
    if product is None:
        raise KeyError(work.product_id)
    work.ctx.catalogue.update_product(
        product.model_copy(
            update={
                "name": package.product_name,
                "manufacturer": manufacturer or product.manufacturer,
                "image_url": image_url or product.image_url,
            }
        )
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
        metadata={"artifactId": evidence_id, "sourceCount": len(source_urls)},
    )
    return {
        "evidence_artifact_id": evidence_id,
        "product_name": package.product_name,
        "manufacturer": manufacturer or "",
        "image_url": image_url or "",
        "known_source_urls": source_urls,
        "source_fingerprint": fingerprint,
    }


def _merge_packages(
    existing: ProductKnowledgePackage,
    incoming: ProductKnowledgePackage,
) -> ProductKnowledgePackage:
    sources = {item.id: item for item in existing.acquired_sources}
    sources.update({item.id: item for item in incoming.acquired_sources})
    evidence = {item.id: item for item in existing.evidence}
    evidence.update({item.id: item for item in incoming.evidence})
    return ProductKnowledgePackage(
        product_id=existing.product_id,
        product_name=existing.product_name or incoming.product_name,
        source_artifact_ids=tuple(
            dict.fromkeys((*existing.source_artifact_ids, *incoming.source_artifact_ids))
        ),
        acquired_sources=tuple(sources.values()),
        evidence=tuple(evidence.values()),
    )


def advance_product(state: MiaWorkflowState) -> dict[str, Any]:
    """Reset run-scoped fields and move to the next selected product URL."""

    queue = state.get("product_queue", ())
    if not queue:
        return {}
    return reset_product_state(product_url=queue[0], product_queue=queue[1:])

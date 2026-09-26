"""Gap-driven source research node."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

from mia_dpp.domain.targets import TemplateIndex
from mia_dpp.runtime.services import ServiceContainer
from mia_dpp.workflow.state import MiaWorkflowState
from mia_dpp.workflow.workspace import RunWorkspace


async def research(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    work = RunWorkspace(state, runtime.context)
    index = work.load_state("targets_artifact_id", TemplateIndex)
    missing = set(state.get("missing_requirement_ids", ()))
    requirements = tuple(item for item in index.requirements if item.id in missing)
    product = work.ctx.catalogue.get_product(work.product_id, user_id=work.user_id)
    domain = None
    if product is not None:
        domain = (urlsplit(product.canonical_url).hostname or "").removeprefix("www.")
    result = await work.ctx.research_agent.research(
        product_id=work.product_id,
        product_name=state.get("product_name") or work.product_id,
        missing_requirements=requirements,
        known_urls=state.get("known_source_urls", ()),
        manufacturer_domain=domain,
    )
    attempts = state.get("research_attempts", 0) + 1
    artifact_id = work.put_json(
        f"research/attempt-{attempts}.json",
        {
            "query": result.query,
            "candidates": [
                item.model_dump(mode="json", by_alias=True) for item in result.candidates
            ],
        },
        derived_from=(work.state_id("coverage_artifact_id"),),
    )
    selected = next((item for item in result.candidates if item.authoritative_domain), None)
    selected = selected or next(iter(result.candidates), None)
    work.event(
        "source.researched",
        f"Research attempt {attempts} found {len(result.candidates)} new source candidates.",
        metadata={"query": result.query, "artifactId": artifact_id},
    )
    if selected is None:
        return {
            "research_attempts": state.get("max_research_attempts", attempts),
            "research_found_source": False,
        }
    return {
        "product_url": selected.url,
        "known_source_urls": tuple(
            dict.fromkeys((*state.get("known_source_urls", ()), selected.url))
        ),
        "research_attempts": attempts,
        "research_found_source": True,
    }

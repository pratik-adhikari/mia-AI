"""Thin LangGraph adapter for gap-driven source research."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

from mia_dpp.runtime.run_context import RunContext
from mia_dpp.runtime.services import ServiceContainer
from mia_dpp.services.source_research import SourceResearchRequest, SourceResearchService
from mia_dpp.workflow.state import MiaWorkflowState


async def research(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    result = await SourceResearchService(
        catalogue=runtime.context.catalogue,
        artifacts=runtime.context.artifacts,
        research_agent=runtime.context.research_agent,
    ).research(
        SourceResearchRequest(
            context=RunContext.from_mapping(state),
            targets_artifact_id=state["targets_artifact_id"],
            coverage_artifact_id=state["coverage_artifact_id"],
            missing_requirement_ids=state.get("missing_requirement_ids", ()),
            product_name=state.get("product_name") or state["product_id"],
            known_source_urls=state.get("known_source_urls", ()),
            research_attempts=int(state.get("research_attempts", 0)),
            max_research_attempts=int(
                state.get("max_research_attempts", state.get("research_attempts", 0) + 1)
            ),
        )
    )
    updates: dict[str, Any] = {
        "research_attempts": result.research_attempts,
        "research_found_source": result.research_found_source,
    }
    if result.product_url is not None:
        updates["product_url"] = result.product_url
    if result.known_source_urls is not None:
        updates["known_source_urls"] = result.known_source_urls
    return updates

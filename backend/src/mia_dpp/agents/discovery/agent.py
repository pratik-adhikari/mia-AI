"""Focused PydanticAI agent that resolves a user request to product URLs."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.models import Model

from mia_dpp.agent.models import AgentRunOutput, AgentStatus
from mia_dpp.agents.discovery.models import DiscoveryState, DiscoveryTurn
from mia_dpp.tools.search import SearchProvider, find_companies, find_products

_INSTRUCTIONS = """Resolve only company and product identity. Do not extract, map, research gaps,
or build a DPP. If the company is ambiguous, search and ask the user to choose. Once a company is
selected, discover official-domain product pages and ask the user to choose when needed. Use only
candidate IDs supplied by tools. When one or more products are selected, finish with status running;
the deterministic LangGraph workflow will take over. Keep replies concise and never invent URLs."""


@dataclass(slots=True)
class DiscoveryDependencies:
    state: DiscoveryState
    search: SearchProvider


async def search_companies(
    ctx: RunContext[DiscoveryDependencies],
    company_name: str,
) -> str:
    candidates = await find_companies(ctx.deps.search, company_name)
    ctx.deps.state.company_candidates = candidates
    ctx.deps.state.status = AgentStatus.AWAITING_COMPANY
    return f"Found {len(candidates)} company candidates; ask the user to choose by candidate ID."


async def select_company(ctx: RunContext[DiscoveryDependencies], company_id: str) -> str:
    candidate = next(
        (item for item in ctx.deps.state.company_candidates if item.id == company_id),
        None,
    )
    if candidate is None:
        return "The company ID is not in the current candidate set."
    ctx.deps.state.selected_company = candidate.model_copy(update={"identity_verified": True})
    ctx.deps.state.status = AgentStatus.RUNNING
    return f"Selected {candidate.name}; discover products next."


async def discover_products(
    ctx: RunContext[DiscoveryDependencies],
    query: str = "",
) -> str:
    company = ctx.deps.state.selected_company
    if company is None:
        return "Select a company before discovering products."
    candidates = await find_products(ctx.deps.search, company, query=query)
    ctx.deps.state.product_candidates = candidates
    ctx.deps.state.status = AgentStatus.AWAITING_PRODUCT
    return f"Found {len(candidates)} official-domain product candidates; ask the user to choose."


async def select_products(
    ctx: RunContext[DiscoveryDependencies],
    product_ids: list[str],
) -> str:
    by_id = {item.id: item for item in ctx.deps.state.product_candidates}
    unknown = [item for item in product_ids if item not in by_id]
    if unknown:
        return f"Unknown product IDs: {', '.join(unknown)}"
    selected = tuple(dict.fromkeys(product_ids))
    urls = tuple(by_id[item].official_url for item in selected)
    ctx.deps.state.selected_product_ids = selected
    ctx.deps.state.product_url = urls[0] if urls else ""
    ctx.deps.state.product_queue = urls[1:]
    ctx.deps.state.status = AgentStatus.RUNNING
    return f"Selected {len(selected)} product(s); the DPP workflow can continue."


class PydanticDiscoveryAgent:
    def __init__(self, model: Model, search: SearchProvider) -> None:
        self._search = search
        self._agent = Agent[
            DiscoveryDependencies,
            AgentRunOutput,
        ](
            model,
            name="mia-product-discovery",
            deps_type=DiscoveryDependencies,
            output_type=AgentRunOutput,
            instructions=_INSTRUCTIONS,
            tools=(
                Tool(search_companies, sequential=True),
                Tool(select_company, sequential=True),
                Tool(discover_products, sequential=True),
                Tool(select_products, sequential=True),
            ),
            retries=2,
        )

    async def run(
        self,
        message: str,
        state: DiscoveryState,
        history_json: str,
    ) -> DiscoveryTurn:
        history = list(ModelMessagesTypeAdapter.validate_json(history_json))
        result = await self._agent.run(
            message,
            deps=DiscoveryDependencies(state=state, search=self._search),
            message_history=history,
        )
        output = result.output
        if state.status is AgentStatus.RUNNING:
            state.status = output.status
        return DiscoveryTurn(
            output=output,
            state=state,
            history_json=ModelMessagesTypeAdapter.dump_json(result.all_messages()).decode(),
        )

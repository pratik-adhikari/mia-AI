"""Focused agent for choosing gap-driven public-source searches."""

from __future__ import annotations

import json

from pydantic_ai import Agent
from pydantic_ai.models import Model

from mia_dpp.agents.research.models import ResearchPlan, ResearchResult
from mia_dpp.domain.targets import Requirement
from mia_dpp.tools.search import SearchProvider, find_product_sources

_INSTRUCTIONS = """Choose one concise web-search query for missing mandatory product data.
Use the supplied official requirement names/descriptions. Prefer manufacturer datasheets, technical
pages, manuals, certificates, or support documents. Do not invent facts or URLs. Return only the
query that would most efficiently resolve the listed gaps."""


class PydanticResearchAgent:
    """Reason about the next query; deterministic Python owns search and filtering."""

    def __init__(self, model: Model, search: SearchProvider) -> None:
        self._search = search
        self._agent = Agent(
            model,
            name="mia-source-researcher",
            output_type=ResearchPlan,
            instructions=_INSTRUCTIONS,
            retries=1,
        )

    async def research(
        self,
        *,
        product_id: str,
        product_name: str,
        missing_requirements: tuple[Requirement, ...],
        known_urls: tuple[str, ...],
        manufacturer_domain: str | None,
    ) -> ResearchResult:
        requirement_context = [
            {
                "name": item.id_short or item.template_path[-1],
                "description": item.description,
                "unit": item.unit,
            }
            for item in missing_requirements
        ]
        prompt = json.dumps(
            {
                "product": product_name,
                "missingRequirements": requirement_context,
                "knownUrls": list(known_urls),
            },
            ensure_ascii=False,
        )
        plan = (await self._agent.run(prompt)).output
        candidates = await find_product_sources(
            self._search,
            product_id=product_id,
            product_name=product_name,
            query=plan.query,
            manufacturer_domain=manufacturer_domain,
        )
        known = set(known_urls)
        return ResearchResult(
            query=plan.query,
            candidates=tuple(item for item in candidates if item.url not in known),
        )


class DeterministicResearchAgent:
    """Model-free fallback used in tests or unconfigured deployments."""

    def __init__(self, search: SearchProvider) -> None:
        self._search = search

    async def research(
        self,
        *,
        product_id: str,
        product_name: str,
        missing_requirements: tuple[Requirement, ...],
        known_urls: tuple[str, ...],
        manufacturer_domain: str | None,
    ) -> ResearchResult:
        query = " ".join(
            item.id_short or item.template_path[-1] for item in missing_requirements[:4]
        ) or "technical datasheet"
        candidates = await find_product_sources(
            self._search,
            product_id=product_id,
            product_name=product_name,
            query=query,
            manufacturer_domain=manufacturer_domain,
        )
        known = set(known_urls)
        return ResearchResult(
            query=query,
            candidates=tuple(item for item in candidates if item.url not in known),
        )

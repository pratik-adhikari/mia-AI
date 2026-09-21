"""LLM selection of additional sources found by Crawl4AI's native deep crawl."""

from __future__ import annotations

import json

from pydantic import Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

from mia_dpp.domain.base import WireModel
from mia_dpp.tools.web.models import SourceLink

_INSTRUCTIONS = """Select every discovered page that may expose additional public product facts,
variant data, images, datasheets, drawings, manuals, certificates, CAD files, or download previews.
Do not discard a page merely because its relevance is uncertain; relevance is decided later. Keep
variant-specific source pages when they may contain different files. Exclude navigation, login,
account creation, cart, legal, privacy, social, and unrelated catalogue pages. Return only candidate
IDs supplied in the input. Never invent an ID or URL."""


class SourceSelection(WireModel):
    """One bounded batch decision over Crawl4AI-grounded source candidates."""

    candidate_ids: tuple[str, ...] = Field(default=(), max_length=12)


class PydanticSourceExplorationPlanner:
    """Use one typed LLM request to select pages; Crawl4AI still performs all crawling."""

    def __init__(self, model: Model) -> None:
        self._agent = Agent(
            model,
            name="mia-source-explorer",
            output_type=SourceSelection,
            instructions=_INSTRUCTIONS,
            retries=1,
        )

    async def select(
        self,
        *,
        seed_url: str,
        product_name: str,
        candidates: tuple[SourceLink, ...],
    ) -> tuple[SourceLink, ...]:
        if not candidates:
            return ()
        aliases = {f"source-{index}": item for index, item in enumerate(candidates, start=1)}
        prompt = json.dumps(
            {
                "seedUrl": seed_url,
                "productName": product_name,
                "candidates": [
                    {"id": identifier, "url": item.url, "text": item.text, "title": item.title}
                    for identifier, item in aliases.items()
                ],
            },
            ensure_ascii=False,
        )
        selection = (await self._agent.run(prompt)).output.candidate_ids
        if len(selection) != len(set(selection)):
            raise ValueError("source exploration returned duplicate candidate IDs")
        unknown = sorted(set(selection) - aliases.keys())
        if unknown:
            raise ValueError(f"source exploration invented candidate IDs: {unknown}")
        return tuple(aliases[identifier] for identifier in selection)

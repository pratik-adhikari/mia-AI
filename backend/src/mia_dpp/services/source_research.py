"""Reusable gap-driven source research planning."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from mia_dpp.agents.research.models import ResearchAgent
from mia_dpp.domain.targets import TemplateIndex
from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.runtime.run_context import RunContext
from mia_dpp.runtime.run_store import RunStore
from mia_dpp.storage.base import ArtifactStore


@dataclass(frozen=True, slots=True)
class SourceResearchRequest:
    context: RunContext
    targets_artifact_id: str
    coverage_artifact_id: str
    missing_requirement_ids: tuple[str, ...]
    product_name: str
    known_source_urls: tuple[str, ...]
    research_attempts: int
    max_research_attempts: int


@dataclass(frozen=True, slots=True)
class SourceResearchResult:
    research_attempts: int
    research_found_source: bool
    product_url: str | None = None
    known_source_urls: tuple[str, ...] | None = None


class SourceResearchService:
    """Select the next source candidate for unresolved requirements."""

    def __init__(
        self,
        *,
        catalogue: ProductCatalogue,
        artifacts: ArtifactStore,
        research_agent: ResearchAgent,
    ) -> None:
        self._catalogue = catalogue
        self._artifacts = artifacts
        self._research_agent = research_agent

    async def research(self, request: SourceResearchRequest) -> SourceResearchResult:
        work = RunStore(request.context, self._catalogue, self._artifacts)
        index = work.load(request.targets_artifact_id, TemplateIndex)
        missing = set(request.missing_requirement_ids)
        requirements = tuple(item for item in index.requirements if item.id in missing)
        product = self._catalogue.get_product(
            request.context.product_id,
            user_id=request.context.user_id,
        )
        domain = None
        if product is not None:
            domain = (urlsplit(product.canonical_url).hostname or "").removeprefix("www.")

        result = await self._research_agent.research(
            product_id=request.context.product_id,
            product_name=request.product_name or request.context.product_id,
            missing_requirements=requirements,
            known_urls=request.known_source_urls,
            manufacturer_domain=domain,
        )
        attempts = request.research_attempts + 1
        artifact_id = work.put_json(
            f"research/attempt-{attempts}.json",
            {
                "query": result.query,
                "candidates": [
                    item.model_dump(mode="json", by_alias=True) for item in result.candidates
                ],
            },
            derived_from=(request.coverage_artifact_id,),
        )
        selected = next(
            (item for item in result.candidates if item.authoritative_domain),
            None,
        )
        selected = selected or next(iter(result.candidates), None)
        work.event(
            "source.researched",
            f"Research attempt {attempts} found {len(result.candidates)} new source candidates.",
            metadata={"query": result.query, "artifactId": artifact_id},
        )
        if selected is None:
            return SourceResearchResult(
                research_attempts=max(request.max_research_attempts, attempts),
                research_found_source=False,
            )
        return SourceResearchResult(
            product_url=selected.url,
            known_source_urls=tuple(dict.fromkeys((*request.known_source_urls, selected.url))),
            research_attempts=attempts,
            research_found_source=True,
        )

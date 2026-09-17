"""Typed source-research results shared by graph and agent implementations."""

from typing import Protocol

from mia_dpp.domain.base import WireModel
from mia_dpp.domain.discovery import ProductSourceCandidate
from mia_dpp.domain.targets import Requirement


class ResearchPlan(WireModel):
    query: str


class ResearchResult(WireModel):
    query: str
    candidates: tuple[ProductSourceCandidate, ...] = ()


class ResearchAgent(Protocol):
    async def research(
        self,
        *,
        product_id: str,
        product_name: str,
        missing_requirements: tuple[Requirement, ...],
        known_urls: tuple[str, ...],
        manufacturer_domain: str | None,
    ) -> ResearchResult: ...

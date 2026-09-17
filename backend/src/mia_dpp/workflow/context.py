"""Non-checkpointed dependencies injected into LangGraph nodes."""

from __future__ import annotations

from dataclasses import dataclass

from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.agents.discovery.models import DiscoveryAgent
from mia_dpp.agents.research.models import ResearchAgent
from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.storage.base import ArtifactStore
from mia_dpp.tools.mapping.models import SemanticMapper
from mia_dpp.tools.mapping.review import MappingReviewService
from mia_dpp.tools.search import SearchProvider
from mia_dpp.tools.web.tool import WebExtractionTool


@dataclass(slots=True)
class MiaContext:
    catalogue: ProductCatalogue
    discovery_agent: DiscoveryAgent | None
    artifacts: ArtifactStore
    templates: OfficialTemplateRepository
    web_tool: WebExtractionTool
    mapping_review: MappingReviewService
    search: SearchProvider
    research_agent: ResearchAgent
    semantic_mapper: SemanticMapper | None = None

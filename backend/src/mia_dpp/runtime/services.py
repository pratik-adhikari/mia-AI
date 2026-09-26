"""Architecture-neutral runtime dependencies shared by every orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field

from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.agents.discovery.models import DiscoveryAgent
from mia_dpp.agents.research.models import ResearchAgent
from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.semantic.decision_policy import DecisionPolicySettings
from mia_dpp.semantic.eclass import EclassPropertyProvider
from mia_dpp.semantic.jev import JevDecisionClient
from mia_dpp.semantic.models import ContextScope
from mia_dpp.storage.base import ArtifactStore
from mia_dpp.tools.mapping.models import SemanticMapper
from mia_dpp.tools.mapping.review import MappingReviewService
from mia_dpp.tools.search import SearchProvider
from mia_dpp.tools.web.tool import WebExtractionTool


@dataclass(slots=True)
class ServiceContainer:
    """Capabilities and adapters shared by graph, agentic, batch, and GUI runtimes."""

    catalogue: ProductCatalogue
    discovery_agent: DiscoveryAgent | None
    artifacts: ArtifactStore
    templates: OfficialTemplateRepository
    web_tool: WebExtractionTool
    mapping_review: MappingReviewService
    search: SearchProvider
    research_agent: ResearchAgent
    semantic_mapper: SemanticMapper | None = None
    jev_decider: JevDecisionClient | None = None
    jev_mapping_enabled: bool = False
    jev_routing_scopes: tuple[ContextScope, ...] = (
        ContextScope.PROPERTY,
        ContextScope.SIBLINGS,
        ContextScope.FULL_PRODUCT,
    )
    jev_routing_max_concurrency: int = 8
    jev_decision_policy: DecisionPolicySettings = field(default_factory=DecisionPolicySettings)
    jev_grouping_scopes: tuple[ContextScope, ...] = (
        ContextScope.SIBLINGS,
        ContextScope.FULL_PRODUCT,
    )
    jev_grouping_max_groups: int = 200
    eclass_shadow_enabled: bool = False
    eclass_provider: EclassPropertyProvider | None = None
    eclass_resolution_scopes: tuple[ContextScope, ...] = (
        ContextScope.SIBLINGS,
        ContextScope.FULL_PRODUCT,
    )
    eclass_candidate_limit: int = 12
    semantic_promotion_enabled: bool = False

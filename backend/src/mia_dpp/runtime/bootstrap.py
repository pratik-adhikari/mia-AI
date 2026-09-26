"""Composition root for concrete MIA runtime capabilities.

Keep provider construction here so the application facade and orchestrators only
consume already-assembled services.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic_ai.models import Model
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.agents.conversation import PydanticConversationSupervisor
from mia_dpp.agents.discovery import PydanticDiscoveryAgent
from mia_dpp.agents.research import DeterministicResearchAgent, PydanticResearchAgent
from mia_dpp.agents.semantic_mapping import PydanticBatchSemanticMapper
from mia_dpp.agents.source_exploration import PydanticSourceExplorationPlanner
from mia_dpp.api.agent_view import AgentResponseView
from mia_dpp.config import Settings
from mia_dpp.integrations.crawl4ai import Crawl4AIPageLoader
from mia_dpp.integrations.ddgs import DdgsSearchProvider
from mia_dpp.persistence.workspace import WorkspaceView
from mia_dpp.runtime.factory import create_artifact_store, create_catalogue
from mia_dpp.runtime.services import ServiceContainer
from mia_dpp.semantic.decision_policy import DecisionPolicySettings
from mia_dpp.semantic.eclass import EclassJsonV2Provider, EclassPropertyProvider
from mia_dpp.semantic.eclass_xml import EclassXmlZipProvider
from mia_dpp.semantic.jev import OpenRouterJevClient
from mia_dpp.services.deep_research import DeepResearchService
from mia_dpp.services.product_query import ProductQueryService
from mia_dpp.tools.mapping.models import SemanticMapper
from mia_dpp.tools.mapping.review import MappingReviewService
from mia_dpp.tools.search import SearchProvider
from mia_dpp.tools.web.tool import WebExtractionTool


@dataclass(slots=True)
class RuntimeAssembly:
    templates: OfficialTemplateRepository
    web_tool: WebExtractionTool
    services: ServiceContainer
    store: WorkspaceView
    query: ProductQueryService
    conversation: PydanticConversationSupervisor | None
    deep_research: DeepResearchService
    response_view: AgentResponseView


def configured_model(settings: Settings) -> Model | None:
    if settings.openrouter_api_key is None:
        return None
    return OpenRouterModel(
        settings.agent_model,
        provider=OpenRouterProvider(
            api_key=settings.openrouter_api_key.get_secret_value(),
            app_url="https://mia-dpp.vercel.app",
            app_title="MIA Digital Product Passport",
        ),
    )


def build_runtime(
    settings: Settings,
    *,
    model: Model | None = None,
    search_provider: SearchProvider | None = None,
    web_tool: WebExtractionTool | None = None,
    semantic_mapper: SemanticMapper | None = None,
    eclass_provider: EclassPropertyProvider | None = None,
) -> RuntimeAssembly:
    """Construct adapters/capabilities once for every orchestration architecture."""

    templates = OfficialTemplateRepository(settings.standards_root)
    agent_model = model or configured_model(settings)
    resolved_web_tool = web_tool or WebExtractionTool(
        loader=Crawl4AIPageLoader(
            model=settings.agent_model,
            api_token=(
                settings.openrouter_api_key.get_secret_value()
                if settings.openrouter_api_key is not None
                else None
            ),
        ),
        source_planner=(
            PydanticSourceExplorationPlanner(agent_model) if agent_model is not None else None
        ),
    )
    search = search_provider or DdgsSearchProvider()
    catalogue = create_catalogue(settings)
    artifacts = create_artifact_store(settings)
    mapping_review = MappingReviewService(templates)
    semantic = semantic_mapper or (
        PydanticBatchSemanticMapper(agent_model) if agent_model is not None else None
    )

    jev_decider: OpenRouterJevClient | None = None
    if settings.jev_mapping_enabled and not settings.jev_shadow_enabled:
        raise ValueError("MIA_JEV_MAPPING_ENABLED requires MIA_JEV_SHADOW_ENABLED")
    if settings.jev_shadow_enabled:
        if settings.openrouter_api_key is None:
            raise ValueError("MIA_JEV_SHADOW_ENABLED requires OPENROUTER_API_KEY")
        jev_decider = OpenRouterJevClient(
            api_key=settings.openrouter_api_key.get_secret_value(),
            model=settings.jev_model,
            max_concurrency=settings.jev_max_concurrency,
        )
    if settings.semantic_promotion_enabled and not settings.eclass_shadow_enabled:
        raise ValueError("MIA_SEMANTIC_PROMOTION_ENABLED requires MIA_ECLASS_SHADOW_ENABLED")

    resolved_eclass_provider = eclass_provider
    if settings.eclass_shadow_enabled:
        if jev_decider is None:
            raise ValueError("MIA_ECLASS_SHADOW_ENABLED requires MIA_JEV_SHADOW_ENABLED")
        if resolved_eclass_provider is None:
            if settings.eclass_provider_mode == "local":
                if not settings.eclass_xml_dictionary_zips:
                    raise ValueError(
                        "MIA_ECLASS_PROVIDER=local requires MIA_ECLASS_XML_DICTIONARY_ZIPS"
                    )
                if settings.eclass_certificate_file is not None:
                    raise ValueError(
                        "Configure local ECLASS XML ZIPs or an ECLASS certificate, not both"
                    )
                dictionary_zips = tuple(
                    Path(item.strip())
                    for item in settings.eclass_xml_dictionary_zips.split(",")
                    if item.strip()
                )
                resolved_eclass_provider = EclassXmlZipProvider(
                    dictionary_zips,
                    language=settings.eclass_xml_language,
                )
            elif settings.eclass_xml_dictionary_zips:
                raise ValueError(
                    "Local ECLASS XML ZIPs are configured but MIA_ECLASS_PROVIDER is not local"
                )
            elif settings.eclass_certificate_file is None:
                raise ValueError(
                    "MIA_ECLASS_SHADOW_ENABLED requires local ECLASS XML ZIPs or "
                    "MIA_ECLASS_CERTIFICATE_FILE"
                )
            else:
                resolved_eclass_provider = EclassJsonV2Provider(
                    certificate_file=settings.eclass_certificate_file,
                    key_file=settings.eclass_key_file,
                    base_url=settings.eclass_json_base_url,
                    search_parameter=settings.eclass_search_parameter,
                )

    discovery = PydanticDiscoveryAgent(agent_model, search) if agent_model is not None else None
    research = (
        PydanticResearchAgent(agent_model, search)
        if agent_model is not None
        else DeterministicResearchAgent(search)
    )
    services = ServiceContainer(
        catalogue=catalogue,
        discovery_agent=discovery,
        artifacts=artifacts,
        templates=templates,
        web_tool=resolved_web_tool,
        mapping_review=mapping_review,
        search=search,
        research_agent=research,
        semantic_mapper=semantic,
        jev_decider=jev_decider,
        jev_mapping_enabled=settings.jev_mapping_enabled,
        jev_routing_max_concurrency=settings.jev_max_concurrency,
        jev_decision_policy=DecisionPolicySettings(
            auto_min_selected_probability=settings.jev_auto_min_selected_probability,
            auto_min_margin=settings.jev_auto_min_margin,
            auto_max_runner_up_ratio=settings.jev_auto_max_runner_up_ratio,
            auto_max_normalized_entropy=settings.jev_auto_max_entropy,
            optional_min_selected_probability=settings.jev_optional_min_selected_probability,
            optional_min_margin=settings.jev_optional_min_margin,
            optional_max_runner_up_ratio=settings.jev_optional_max_runner_up_ratio,
            optional_max_normalized_entropy=settings.jev_optional_max_entropy,
        ),
        jev_grouping_max_groups=settings.jev_grouping_max_groups,
        eclass_shadow_enabled=settings.eclass_shadow_enabled,
        eclass_provider=resolved_eclass_provider,
        eclass_candidate_limit=settings.eclass_candidate_limit,
        semantic_promotion_enabled=settings.semantic_promotion_enabled,
    )
    store = WorkspaceView(catalogue, artifacts)
    query = ProductQueryService(catalogue, artifacts)
    conversation = (
        PydanticConversationSupervisor(agent_model, query) if agent_model is not None else None
    )
    deep_research = DeepResearchService(services)
    return RuntimeAssembly(
        templates=templates,
        web_tool=resolved_web_tool,
        services=services,
        store=store,
        query=query,
        conversation=conversation,
        deep_research=deep_research,
        response_view=AgentResponseView(services, store),
    )

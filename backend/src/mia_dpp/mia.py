"""Small application façade over MIA's durable LangGraph workflow."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic_ai.models import Model
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.agent.models import (
    AgentRequest,
    AgentResponse,
    AgentReviewRequest,
    AgentStatus,
    AgentValueRequest,
)
from mia_dpp.agents.discovery import PydanticDiscoveryAgent
from mia_dpp.agents.research import DeterministicResearchAgent, PydanticResearchAgent
from mia_dpp.agents.semantic_mapping import PydanticBatchSemanticMapper
from mia_dpp.api.agent_view import AgentResponseView
from mia_dpp.config import Settings
from mia_dpp.domain.product import MessageRole
from mia_dpp.integrations.crawl4ai import Crawl4AIPageLoader
from mia_dpp.integrations.ddgs import DdgsSearchProvider
from mia_dpp.persistence.workspace import WorkspaceView
from mia_dpp.runtime.checkpoints import open_checkpointer
from mia_dpp.runtime.factory import create_artifact_store, create_catalogue
from mia_dpp.tools.mapping.models import SemanticMapper
from mia_dpp.tools.mapping.review import MappingReviewService
from mia_dpp.tools.search import SearchProvider
from mia_dpp.tools.web.tool import WebExtractionTool
from mia_dpp.workflow.context import MiaContext
from mia_dpp.workflow.graph import create_graph
from mia_dpp.workflow.state import MiaWorkflowState


class Mia:
    """Compose MIA; LangGraph owns workflow order, persistence, and HITL."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        model: Model | None = None,
        search_provider: SearchProvider | None = None,
        web_tool: WebExtractionTool | None = None,
        semantic_mapper: SemanticMapper | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.templates = OfficialTemplateRepository(self.settings.standards_root)
        self.web_tool = web_tool or WebExtractionTool(loader=Crawl4AIPageLoader())
        search = search_provider or DdgsSearchProvider()
        catalogue = create_catalogue(self.settings)
        artifacts = create_artifact_store(self.settings)
        mapping_review = MappingReviewService(self.templates)

        agent_model = model or self._configured_model()
        semantic = semantic_mapper or (
            PydanticBatchSemanticMapper(agent_model) if agent_model is not None else None
        )
        discovery = PydanticDiscoveryAgent(agent_model, search) if agent_model is not None else None
        research = (
            PydanticResearchAgent(agent_model, search)
            if agent_model is not None
            else DeterministicResearchAgent(search)
        )
        self.context = MiaContext(
            catalogue=catalogue,
            discovery_agent=discovery,
            artifacts=artifacts,
            templates=self.templates,
            web_tool=self.web_tool,
            mapping_review=mapping_review,
            search=search,
            research_agent=research,
            semantic_mapper=semantic,
        )
        self.store = WorkspaceView(catalogue, artifacts)
        self._response_view = AgentResponseView(self.context, self.store)
        self._graph: Any | None = None
        self._checkpoint_cm: Any | None = None

    @property
    def configured(self) -> bool:
        return self.context.semantic_mapper is not None

    async def message(self, request: AgentRequest) -> AgentResponse:
        thread_id = request.thread_id or f"thread-{uuid.uuid4().hex}"
        graph = await self._ensure_graph()
        config = self._config(thread_id)
        snapshot = await graph.aget_state(config)
        if self._snapshot_interrupt(snapshot) is not None:
            return self._response_view.build(dict(snapshot.values), trace_offset=0)

        trace_offset = len(self.store.list_events(thread_id))
        self.context.catalogue.add_message(thread_id, MessageRole.USER, request.message)
        initial = not bool(snapshot.values)
        update: dict[str, Any] = {
            "thread_id": thread_id,
            "user_message": request.message,
        }
        if initial:
            update.update(
                {
                    "discovery_history_json": "[]",
                    "target_submodels": ("digital_nameplate", "technical_data"),
                    "max_research_attempts": 2,
                    "research_attempts": 0,
                    "status": "running",
                }
            )
        if not self.configured and "http" in request.message.casefold():
            response = AgentResponse(
                thread_id=thread_id,
                reply="Configure OPENROUTER_API_KEY to run semantic DPP mapping.",
                status=AgentStatus.AWAITING_INPUT,
                decision_summary="No semantic model is configured.",
            )
            self._record_assistant(response)
            return response

        result = await graph.ainvoke(update, config=config, context=self.context)
        response = self._response_view.build(dict(result), trace_offset=trace_offset)
        self._record_assistant(response)
        return response

    async def review(self, request: AgentReviewRequest) -> AgentResponse:
        return await self._resume(
            request.thread_id,
            request.model_dump(mode="json"),
            message="Mapping review submitted.",
        )

    async def provide_value(self, request: AgentValueRequest) -> AgentResponse:
        return await self._resume(
            request.thread_id,
            request.model_dump(mode="json"),
            message="Requested product value supplied.",
        )

    async def close(self) -> None:
        if self._checkpoint_cm is not None:
            await self._checkpoint_cm.__aexit__(None, None, None)
            self._checkpoint_cm = None
            self._graph = None

    async def _resume(
        self,
        thread_id: str,
        payload: dict[str, Any],
        *,
        message: str,
    ) -> AgentResponse:
        from langgraph.types import Command

        graph = await self._ensure_graph()
        config = self._config(thread_id)
        trace_offset = len(self.store.list_events(thread_id))
        self.context.catalogue.add_message(thread_id, MessageRole.USER, message)
        result = await graph.ainvoke(
            Command(resume=payload),
            config=config,
            context=self.context,
        )
        response = self._response_view.build(dict(result), trace_offset=trace_offset)
        self._record_assistant(response)
        return response

    async def _ensure_graph(self):
        if self._graph is None:
            self._checkpoint_cm = open_checkpointer(self.settings)
            checkpointer = await self._checkpoint_cm.__aenter__()
            self._graph = create_graph(checkpointer)
        return self._graph

    def _configured_model(self) -> Model | None:
        if self.settings.openrouter_api_key is None:
            return None
        return OpenRouterModel(
            self.settings.agent_model,
            provider=OpenRouterProvider(
                api_key=self.settings.openrouter_api_key.get_secret_value(),
                app_url="https://mia-dpp.vercel.app",
                app_title="MIA Digital Product Passport",
            ),
        )

    def _record_assistant(self, response: AgentResponse) -> None:
        runs = self.context.catalogue.list_runs_for_thread(response.thread_id)
        self.context.catalogue.add_message(
            response.thread_id,
            MessageRole.ASSISTANT,
            response.reply,
            run_id=runs[-1].id if runs else None,
        )

    @staticmethod
    def _snapshot_interrupt(snapshot: Any) -> object | None:
        for task in getattr(snapshot, "tasks", ()):
            interrupts = getattr(task, "interrupts", ())
            if interrupts:
                return interrupts[0]
        return None

    @staticmethod
    def _config(thread_id: str) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": thread_id}}

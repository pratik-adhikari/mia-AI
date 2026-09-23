"""Small application façade over MIA's durable LangGraph workflow."""

from __future__ import annotations

import uuid
from typing import Any, cast

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
from mia_dpp.agents.source_exploration import PydanticSourceExplorationPlanner
from mia_dpp.api.agent_view import AgentResponseView
from mia_dpp.config import Settings
from mia_dpp.domain.product import BackgroundJob, MessageRole, ProductRun, RunStatus
from mia_dpp.integrations.crawl4ai import Crawl4AIPageLoader
from mia_dpp.integrations.ddgs import DdgsSearchProvider
from mia_dpp.persistence.catalogue import LOCAL_USER_ID
from mia_dpp.persistence.workspace import WorkspaceView
from mia_dpp.runtime.checkpoints import open_checkpointer
from mia_dpp.runtime.factory import create_artifact_store, create_catalogue
from mia_dpp.services.deep_research import DeepResearchService
from mia_dpp.tools.mapping.models import SemanticMapper
from mia_dpp.tools.mapping.review import MappingReviewService
from mia_dpp.tools.search import SearchProvider
from mia_dpp.tools.web.tool import WebExtractionTool
from mia_dpp.workflow.context import MiaContext
from mia_dpp.workflow.graph import create_graph


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
        agent_model = model or self._configured_model()
        # Credentials stay inside the Crawl4AI adapter; callers still receive only MIA models.
        self.web_tool = web_tool or WebExtractionTool(
            loader=Crawl4AIPageLoader(
                model=self.settings.agent_model,
                api_token=(
                    self.settings.openrouter_api_key.get_secret_value()
                    if self.settings.openrouter_api_key is not None
                    else None
                ),
            ),
            source_planner=(
                PydanticSourceExplorationPlanner(agent_model) if agent_model is not None else None
            ),
        )
        search = search_provider or DdgsSearchProvider()
        catalogue = create_catalogue(self.settings)
        artifacts = create_artifact_store(self.settings)
        mapping_review = MappingReviewService(self.templates)

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
        self.deep_research = DeepResearchService(self.context)
        self._response_view = AgentResponseView(self.context, self.store)
        self._graph: Any | None = None
        self._checkpoint_cm: Any | None = None

    @property
    def configured(self) -> bool:
        return self.context.semantic_mapper is not None

    async def message(
        self,
        request: AgentRequest,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> AgentResponse:
        thread_id = request.thread_id or f"thread-{uuid.uuid4().hex}"
        self.context.catalogue.get_or_create_thread(
            thread_id,
            user_id,
            title=request.message[:120],
        )
        graph = await self._ensure_graph()
        config = self._config(thread_id, user_id)
        snapshot = await graph.aget_state(config)
        trace_offset = len(self.store.list_events(thread_id, user_id=user_id))
        message = self.context.catalogue.add_message(
            thread_id,
            MessageRole.USER,
            request.message,
            user_id=user_id,
        )
        if self._snapshot_interrupt(snapshot) is not None:
            self._assign_message_to_latest_run(message.id, thread_id, user_id=user_id)
            response = self._response_view.build(dict(snapshot.values), trace_offset=trace_offset)
            self._record_assistant(response, user_id=user_id)
            return response

        initial = not bool(snapshot.values)
        update: dict[str, Any] = {
            "thread_id": thread_id,
            "user_id": user_id,
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
            self._record_assistant(response, user_id=user_id)
            return response

        try:
            result = await graph.ainvoke(update, config=config, context=self.context)
        except Exception as error:
            self._assign_message_to_latest_run(message.id, thread_id, user_id=user_id)
            self._record_failure(thread_id, error, user_id=user_id)
            raise
        self._assign_message_to_latest_run(message.id, thread_id, user_id=user_id)
        response = self._response_view.build(dict(result), trace_offset=trace_offset)
        self._record_assistant(response, user_id=user_id)
        return response

    async def review(
        self,
        request: AgentReviewRequest,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> AgentResponse:
        return await self._resume(
            request.thread_id,
            request.model_dump(mode="json"),
            message="Mapping review submitted.",
            user_id=user_id,
        )

    async def provide_value(
        self,
        request: AgentValueRequest,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> AgentResponse:
        return await self._resume(
            request.thread_id,
            request.model_dump(mode="json"),
            message="Requested product value supplied.",
            user_id=user_id,
        )

    async def thread_state(
        self,
        thread_id: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> AgentResponse:
        """Restore the authoritative checkpoint-backed workspace state for one owned thread."""

        if self.context.catalogue.get_thread(thread_id, user_id=user_id) is None:
            raise KeyError(thread_id)
        graph = await self._ensure_graph()
        snapshot = await graph.aget_state(self._config(thread_id, user_id))
        if not snapshot.values:
            return AgentResponse(
                thread_id=thread_id,
                reply="",
                status=AgentStatus.AWAITING_INPUT,
                decision_summary="Conversation exists but has no active workflow state.",
            )
        return self._response_view.build(dict(snapshot.values), trace_offset=0)

    async def close(self) -> None:
        if self._checkpoint_cm is not None:
            await self._checkpoint_cm.__aexit__(None, None, None)
            self._checkpoint_cm = None
            self._graph = None

    async def run_deep_research(self, job_id: str, *, user_id: str) -> BackgroundJob:
        """Run one durable worker invocation against catalogue-owned job state."""

        return await self.deep_research.run(job_id, user_id=user_id)

    async def _resume(
        self,
        thread_id: str,
        payload: dict[str, Any],
        *,
        message: str,
        user_id: str,
    ) -> AgentResponse:
        from langgraph.types import Command

        graph = await self._ensure_graph()
        if self.context.catalogue.get_thread(thread_id, user_id=user_id) is None:
            raise ValueError("unknown thread")
        config = self._config(thread_id, user_id)
        trace_offset = len(self.store.list_events(thread_id, user_id=user_id))
        user_message = self.context.catalogue.add_message(
            thread_id,
            MessageRole.USER,
            message,
            user_id=user_id,
        )
        self._assign_message_to_latest_run(user_message.id, thread_id, user_id=user_id)
        try:
            result = await graph.ainvoke(
                Command(resume=payload),
                config=config,
                context=self.context,
            )
        except ValueError as error:
            self._record_rejected_input(thread_id, error, user_id=user_id)
            raise
        except Exception as error:
            self._record_failure(thread_id, error, user_id=user_id)
            raise
        response = self._response_view.build(dict(result), trace_offset=trace_offset)
        self._record_assistant(response, user_id=user_id)
        return response

    async def _ensure_graph(self) -> Any:
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

    def _record_assistant(self, response: AgentResponse, *, user_id: str) -> None:
        run = self._latest_run(response.thread_id, user_id=user_id)
        self.context.catalogue.add_message(
            response.thread_id,
            MessageRole.ASSISTANT,
            response.reply,
            run_id=run.id if run else None,
            user_id=user_id,
        )

    def _assign_message_to_latest_run(
        self,
        message_id: str,
        thread_id: str,
        *,
        user_id: str,
    ) -> None:
        run = self._latest_run(thread_id, user_id=user_id)
        if run is not None:
            self.context.catalogue.assign_message_to_run(message_id, run.id)

    def _record_failure(self, thread_id: str, error: Exception, *, user_id: str) -> None:
        run = self._latest_active_run(thread_id, user_id=user_id)
        if run is None:
            return
        detail = str(error) or type(error).__name__
        self.context.catalogue.add_event(
            run.id,
            "workflow.failed",
            "Workflow execution failed.",
            metadata={"error": detail, "errorType": type(error).__name__},
        )
        self.context.catalogue.finish_run(run.id, RunStatus.FAILED, error=detail)

    def _record_rejected_input(
        self,
        thread_id: str,
        error: ValueError,
        *,
        user_id: str,
    ) -> None:
        run = self._latest_active_run(thread_id, user_id=user_id)
        if run is not None:
            self.context.catalogue.add_event(
                run.id,
                "workflow.input_rejected",
                "Rejected invalid human input without advancing the workflow.",
                metadata={"error": str(error)},
            )

    def _latest_active_run(self, thread_id: str, *, user_id: str) -> ProductRun | None:
        active = {RunStatus.RUNNING, RunStatus.AWAITING_HUMAN}
        return next(
            (
                run
                for run in reversed(
                    self.context.catalogue.list_runs_for_thread(thread_id, user_id=user_id)
                )
                if run.status in active
            ),
            None,
        )

    def _latest_run(self, thread_id: str, *, user_id: str) -> ProductRun | None:
        runs = self.context.catalogue.list_runs_for_thread(thread_id, user_id=user_id)
        return runs[-1] if runs else None

    @staticmethod
    def _snapshot_interrupt(snapshot: Any) -> object | None:
        for task in getattr(snapshot, "tasks", ()):
            interrupts = getattr(task, "interrupts", ())
            if interrupts:
                return cast(object, interrupts[0])
        return None

    @staticmethod
    def _config(thread_id: str, user_id: str) -> dict[str, dict[str, str]]:
        # Clerk users may choose the same client-side thread ID; checkpoint keys must not collide.
        return {"configurable": {"thread_id": f"{user_id}:{thread_id}"}}

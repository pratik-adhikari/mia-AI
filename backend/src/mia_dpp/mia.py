"""Small application façade over MIA's durable LangGraph workflow."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

import httpx
from langgraph_sdk import get_client
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
from mia_dpp.persistence.catalogue import ActiveProductRunExists, LOCAL_USER_ID
from mia_dpp.persistence.workspace import WorkspaceView
from mia_dpp.runtime.checkpoints import open_checkpointer
from mia_dpp.runtime.factory import create_artifact_store, create_catalogue
from mia_dpp.semantic.decision_policy import DecisionPolicySettings
from mia_dpp.semantic.jev import OpenRouterJevClient
from mia_dpp.services.deep_research import DeepResearchService
from mia_dpp.tools.mapping.models import SemanticMapper
from mia_dpp.tools.mapping.review import MappingReviewService
from mia_dpp.tools.search import SearchProvider
from mia_dpp.tools.web.tool import WebExtractionTool
from mia_dpp.workflow.context import MiaContext
from mia_dpp.workflow.graph import create_graph
from mia_dpp.workflow.identity import direct_product_url
from mia_dpp.workflow.state import reset_product_state


@dataclass
class _GraphDebugSession:
    run_id: str
    status: str = "running"
    events: list[dict[str, Any]] = field(default_factory=list)
    listeners: set[asyncio.Queue[dict[str, Any] | None]] = field(default_factory=set)


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
        jev_decider: OpenRouterJevClient | None = None
        if self.settings.jev_shadow_enabled:
            if self.settings.openrouter_api_key is None:
                raise ValueError(
                    "MIA_JEV_SHADOW_ENABLED requires OPENROUTER_API_KEY"
                )
            jev_decider = OpenRouterJevClient(
                api_key=self.settings.openrouter_api_key.get_secret_value(),
                model=self.settings.jev_model,
                max_concurrency=self.settings.jev_max_concurrency,
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
            jev_decider=jev_decider,
            jev_routing_max_concurrency=self.settings.jev_max_concurrency,
            jev_decision_policy=DecisionPolicySettings(
                auto_min_selected_probability=(
                    self.settings.jev_auto_min_selected_probability
                ),
                auto_min_margin=self.settings.jev_auto_min_margin,
                auto_max_runner_up_ratio=self.settings.jev_auto_max_runner_up_ratio,
                auto_max_normalized_entropy=self.settings.jev_auto_max_entropy,
                optional_min_selected_probability=(
                    self.settings.jev_optional_min_selected_probability
                ),
                optional_min_margin=self.settings.jev_optional_min_margin,
                optional_max_runner_up_ratio=(
                    self.settings.jev_optional_max_runner_up_ratio
                ),
                optional_max_normalized_entropy=(
                    self.settings.jev_optional_max_entropy
                ),
            ),
        )
        self.store = WorkspaceView(catalogue, artifacts)
        self.deep_research = DeepResearchService(self.context)
        self._response_view = AgentResponseView(self.context, self.store)
        self._graph: Any | None = None
        self._checkpoint_cm: Any | None = None
        self._agent_client: Any | None = None
        self._graph_debug_sessions: dict[tuple[str, str], _GraphDebugSession] = {}

    @property
    def configured(self) -> bool:
        return self.context.semantic_mapper is not None

    async def message(
        self,
        request: AgentRequest,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> AgentResponse:
        requested_thread_id = request.thread_id
        active_product_run: ProductRun | None = None
        redirected_to_active_thread = False
        direct_url = direct_product_url(request.message)
        if direct_url is not None:
            product, _ = self.context.catalogue.get_or_create_product(
                direct_url,
                user_id=user_id,
            )
            active = self.context.catalogue.latest_active_run(product.id, user_id=user_id)
            if active is not None:
                active_product_run = active
                redirected_to_active_thread = active.thread_id != requested_thread_id
                thread_id = active.thread_id
            else:
                thread_id = requested_thread_id or f"thread-{uuid.uuid4().hex}"
        else:
            thread_id = requested_thread_id or f"thread-{uuid.uuid4().hex}"

        thread_exists = self.context.catalogue.get_thread(thread_id, user_id=user_id) is not None
        self.context.catalogue.get_or_create_thread(
            thread_id,
            user_id,
            title=request.message[:120],
        )
        remote = self._use_agent_server
        snapshot: Any | None = None
        if remote:
            snapshot = await self._agent_server_snapshot(thread_id, user_id)
            if snapshot is None and not thread_exists:
                await self._agent_server_client().threads.create(
                    thread_id=self._agent_server_thread_id(thread_id, user_id),
                    if_exists="do_nothing",
                )
                snapshot = {"values": {}, "tasks": []}
            elif snapshot is None:
                # Existing local SQLite threads predate Agent Server ownership. Keep them
                # resumable on their original checkpoint rather than silently forking state.
                remote = False
        if not remote:
            graph = await self._ensure_graph()
            config = self._config(thread_id, user_id)
            snapshot = await graph.aget_state(config)
        assert snapshot is not None
        trace_offset = len(self.store.list_events(thread_id, user_id=user_id))
        message = self.context.catalogue.add_message(
            thread_id,
            MessageRole.USER,
            request.message,
            user_id=user_id,
        )
        values = self._snapshot_values(snapshot)

        if active_product_run is not None:
            lease_live = self.context.catalogue.run_lease_is_live(active_product_run)
            if (
                request.refresh_requested
                and active_product_run.status is RunStatus.RUNNING
                and lease_live
            ):
                self.context.catalogue.request_thread_refresh(thread_id, user_id=user_id)
                self.context.catalogue.assign_message_to_run(message.id, active_product_run.id)
                if values:
                    response = self._response_view.build(values, trace_offset=trace_offset)
                else:
                    response = AgentResponse(
                        thread_id=active_product_run.thread_id,
                        reply="Existing work for this product is still running.",
                        status=AgentStatus.RUNNING,
                        decision_summary="Source refresh queued behind the live product execution.",
                    )
                response = response.model_copy(
                    update={
                        "thread_id": active_product_run.thread_id,
                        "reply": (
                            "Source refresh is queued in this same chat and will start as soon as "
                            "the current live execution safely returns.\n\n" + response.reply
                        ),
                        "decision_summary": (
                            "Kept the live execution and queued refresh instead of terminating it."
                        ),
                    }
                )
                self._record_assistant(response, user_id=user_id)
                return response

            should_restart = request.refresh_requested or (
                active_product_run.status is RunStatus.RUNNING and not lease_live
            )
            if should_restart:
                reason = (
                    "Source refresh requested; restarted product work in the same chat."
                    if request.refresh_requested
                    else (
                        "Recovered a RUNNING run only after its execution lease expired, "
                        "using a new workflow generation in the same chat."
                    )
                )
                response = await self._restart_product_work_in_same_thread(
                    thread_id=thread_id,
                    user_id=user_id,
                    active_run=active_product_run,
                    product_url=direct_url or "",
                    user_message=request.message,
                    refresh_requested=request.refresh_requested,
                    previous_values=values,
                    trace_offset=trace_offset,
                    message_id=message.id,
                    reason=reason,
                    allow_terminal=False,
                )
                if redirected_to_active_thread:
                    response = response.model_copy(
                        update={
                            "reply": (
                                "This product already had an active chat, so I continued there.\n\n"
                                + response.reply
                            )
                        }
                    )
                return response

            self.context.catalogue.assign_message_to_run(message.id, active_product_run.id)
            if values:
                response = self._response_view.build(values, trace_offset=trace_offset)
            else:
                response = AgentResponse(
                    thread_id=active_product_run.thread_id,
                    reply="Existing work for this product is already active.",
                    status=(
                        AgentStatus.AWAITING_REVIEW
                        if active_product_run.status is RunStatus.AWAITING_HUMAN
                        else AgentStatus.RUNNING
                    ),
                    decision_summary="Reused the active product workflow.",
                )
            response = response.model_copy(
                update={
                    "thread_id": active_product_run.thread_id,
                    "reply": (
                        (
                            "This product already has active work, so I merged this request into "
                            "the existing chat.\n\n"
                        )
                        if redirected_to_active_thread
                        else ""
                    )
                    + response.reply,
                    "decision_summary": (
                        "The existing active product workflow was reused instead of starting "
                        "a concurrent run."
                    ),
                }
            )
            self._record_assistant(response, user_id=user_id)
            return response

        if self._snapshot_interrupt(snapshot) is not None:
            self._assign_message_to_latest_run(message.id, thread_id, user_id=user_id)
            response = self._response_view.build(values, trace_offset=trace_offset)
            self._record_assistant(response, user_id=user_id)
            return response

        initial = not bool(values)
        update: dict[str, Any] = {
            "thread_id": thread_id,
            "user_id": user_id,
            "user_message": request.message,
            "refresh_requested": request.refresh_requested,
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
        invocation_thread = self.context.catalogue.get_thread(thread_id, user_id=user_id)
        invocation_generation = (
            invocation_thread.workflow_generation if invocation_thread is not None else 0
        )
        invocation_run = (
            self.context.catalogue.get_run(str(values["run_id"]))
            if values.get("run_id")
            else self.context.catalogue.run_for_thread_generation(
                thread_id,
                invocation_generation,
                user_id=user_id,
            )
        )
        invocation_run_id = invocation_run.id if invocation_run is not None else None

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
            if remote:
                result = await self._run_agent_server(thread_id, user_id, run_input=update)
            else:
                result = await graph.ainvoke(update, config=config, context=self.context)
        except ActiveProductRunExists as conflict:
            active = conflict.run
            self.context.catalogue.add_message(
                active.thread_id,
                MessageRole.USER,
                request.message,
                run_id=active.id,
                user_id=user_id,
            )
            if not thread_exists and thread_id != active.thread_id:
                self.context.catalogue.delete_thread(thread_id, user_id=user_id)
            response = await self.thread_state(active.thread_id, user_id=user_id)
            response = response.model_copy(
                update={
                    "thread_id": active.thread_id,
                    "reply": (
                        "This product already has active work, so I merged this request into "
                        "the existing chat.\n\n" + response.reply
                    ),
                    "decision_summary": (
                        "A concurrent product run was prevented and the existing chat was reused."
                    ),
                }
            )
            self._record_assistant(response, user_id=user_id)
            return response
        except Exception as error:
            if invocation_run_id is not None:
                self.context.catalogue.assign_message_to_run(message.id, invocation_run_id)
                self._record_failure(
                    thread_id,
                    error,
                    user_id=user_id,
                    run_id=invocation_run_id,
                )
            raise
        result_values = dict(result)
        result_run_id = result_values.get("run_id")
        if result_run_id:
            self.context.catalogue.assign_message_to_run(message.id, str(result_run_id))
        thread = self.context.catalogue.get_thread(thread_id, user_id=user_id)
        if (
            thread is not None
            and thread.pending_refresh_requested
            and result_values.get("product_id")
            and result_values.get("run_id")
            and result_values.get("product_url")
        ):
            completed_run = self.context.catalogue.get_run(str(result_values["run_id"]))
            if completed_run is not None:
                return await self._restart_product_work_in_same_thread(
                    thread_id=thread_id,
                    user_id=user_id,
                    active_run=completed_run,
                    product_url=str(result_values["product_url"]),
                    user_message="Refresh sources requested while previous product work was running.",
                    refresh_requested=True,
                    previous_values=result_values,
                    trace_offset=trace_offset,
                    message_id=None,
                    reason="Queued source refresh started after the live execution returned.",
                    allow_terminal=True,
                )
        response = self._response_view.build(result_values, trace_offset=trace_offset)
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
        snapshot = (
            await self._agent_server_snapshot(thread_id, user_id)
            if self._use_agent_server
            else None
        )
        if snapshot is None:
            graph = await self._ensure_graph()
            snapshot = await graph.aget_state(self._config(thread_id, user_id))
        values = self._snapshot_values(snapshot)
        if not values:
            return AgentResponse(
                thread_id=thread_id,
                reply="",
                status=AgentStatus.AWAITING_INPUT,
                decision_summary="Conversation exists but has no active workflow state.",
            )
        return self._response_view.build(values, trace_offset=0)

    async def close(self) -> None:
        if self._agent_client is not None:
            await self._agent_client.aclose()
            self._agent_client = None
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
        if self.context.catalogue.get_thread(thread_id, user_id=user_id) is None:
            raise ValueError("unknown thread")
        trace_offset = len(self.store.list_events(thread_id, user_id=user_id))
        user_message = self.context.catalogue.add_message(
            thread_id,
            MessageRole.USER,
            message,
            user_id=user_id,
        )
        self._assign_message_to_latest_run(user_message.id, thread_id, user_id=user_id)
        failing_run_id: str | None = None
        try:
            snapshot = (
                await self._agent_server_snapshot(thread_id, user_id)
                if self._use_agent_server
                else None
            )
            if snapshot is not None:
                snapshot_values = self._snapshot_values(snapshot)
                failing_run_id = (
                    str(snapshot_values["run_id"])
                    if snapshot_values.get("run_id")
                    else None
                )
                result = await self._run_agent_server(
                    thread_id, user_id, command={"resume": payload}
                )
            else:
                from langgraph.types import Command

                graph = await self._ensure_graph()
                local_snapshot = await graph.aget_state(self._config(thread_id, user_id))
                snapshot_values = self._snapshot_values(local_snapshot)
                failing_run_id = (
                    str(snapshot_values["run_id"])
                    if snapshot_values.get("run_id")
                    else None
                )
                result = await graph.ainvoke(
                    Command(resume=payload),
                    config=self._config(thread_id, user_id),
                    context=self.context,
                )
        except ValueError as error:
            self._record_rejected_input(
                thread_id,
                error,
                user_id=user_id,
                run_id=failing_run_id,
            )
            raise
        except Exception as error:
            self._record_failure(
                thread_id,
                error,
                user_id=user_id,
                run_id=failing_run_id,
            )
            raise
        response = self._response_view.build(dict(result), trace_offset=trace_offset)
        self._record_assistant(response, user_id=user_id)
        return response

    async def _restart_product_work_in_same_thread(
        self,
        *,
        thread_id: str,
        user_id: str,
        active_run: ProductRun,
        product_url: str,
        user_message: str,
        refresh_requested: bool,
        previous_values: dict[str, Any],
        trace_offset: int,
        message_id: str | None,
        reason: str,
        allow_terminal: bool,
    ) -> AgentResponse:
        """Recover or refresh product work atomically in the same visible chat."""

        thread = self.context.catalogue.get_thread(thread_id, user_id=user_id)
        if thread is None:
            raise KeyError(thread_id)
        durable = self.context.catalogue.get_product_work_snapshot(
            active_run.product_id,
            user_id=user_id,
        )
        replacement = self.context.catalogue.claim_product_restart(
            user_id=user_id,
            product_id=active_run.product_id,
            expected_run_id=active_run.id,
            expected_generation=thread.workflow_generation,
            reason=reason,
            refresh_requested=refresh_requested,
            require_expired_lease=(
                active_run.status is RunStatus.RUNNING and not allow_terminal
            ),
            allow_terminal=allow_terminal,
        )
        update: dict[str, Any] = {
            **reset_product_state(product_url=product_url),
            "thread_id": thread_id,
            "user_id": user_id,
            "user_message": user_message,
            "product_id": replacement.product_id,
            "run_id": replacement.id,
            "workflow_generation": replacement.workflow_generation,
            "source_generation": (
                (
                    durable.source_generation + 1
                    if refresh_requested
                    else durable.source_generation
                )
                if durable is not None
                else 1
            ),
            "refresh_requested": refresh_requested,
            "reuse_mode": (
                "refresh_sources" if refresh_requested else "continue_saved_work"
            ),
            "reuse_prior_work": bool(
                not refresh_requested and durable is not None and durable.evidence_artifact_id
            ),
            "seeded_from_run_id": active_run.id,
            "evidence_artifact_id": (
                durable.evidence_artifact_id
                if durable is not None and durable.evidence_artifact_id
                else ""
            ),
            "reviewed_mapping_artifact_id": (
                durable.reviewed_mapping_artifact_id
                if durable is not None and durable.reviewed_mapping_artifact_id
                else ""
            ),
            "product_snapshot_version": durable.version if durable is not None else 0,
            "discovery_history_json": previous_values.get("discovery_history_json", "[]"),
            "target_submodels": previous_values.get(
                "target_submodels",
                ("digital_nameplate", "technical_data"),
            ),
            "max_research_attempts": previous_values.get("max_research_attempts", 2),
            "research_attempts": 0,
            "status": "running",
        }
        try:
            if self._use_agent_server:
                result = await self._run_agent_server(thread_id, user_id, run_input=update)
            else:
                graph = await self._ensure_graph()
                result = await graph.ainvoke(
                    update,
                    config=self._config(thread_id, user_id),
                    context=self.context,
                )
        except Exception as error:
            self._record_failure(
                thread_id,
                error,
                user_id=user_id,
                run_id=replacement.id,
            )
            raise
        if message_id is not None:
            self.context.catalogue.assign_message_to_run(message_id, replacement.id)
        response = self._response_view.build(dict(result), trace_offset=trace_offset)
        response = response.model_copy(
            update={
                "thread_id": thread_id,
                "decision_summary": reason,
            }
        )
        self._record_assistant(response, user_id=user_id)
        return response

    async def _ensure_graph(self) -> Any:
        if self._graph is None:
            self._checkpoint_cm = open_checkpointer(self.settings)
            checkpointer = await self._checkpoint_cm.__aenter__()
            self._graph = create_graph(checkpointer)
        return self._graph

    @property
    def _use_agent_server(self) -> bool:
        return bool(
            self.settings.local_mode
            and not self.settings.vercel_environment
            and self.settings.agent_server_url
        )

    def _agent_server_client(self) -> Any:
        if self._agent_client is None:
            if not self.settings.agent_server_url:
                raise RuntimeError("MIA_AGENT_SERVER_URL is not configured.")
            self._agent_client = get_client(url=self.settings.agent_server_url)
        return self._agent_client

    def _agent_server_thread_id(self, thread_id: str, user_id: str) -> str:
        # Workflow generations let one visible chat restart safely without reusing an old checkpoint.
        thread = self.context.catalogue.get_thread(thread_id, user_id=user_id)
        generation = thread.workflow_generation if thread is not None else 0
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"mia-dpp:{user_id}:{thread_id}:generation:{generation}",
            )
        )

    async def _agent_server_snapshot(self, thread_id: str, user_id: str) -> Any | None:
        if not self._use_agent_server:
            return None
        try:
            return await self._agent_server_client().threads.get_state(
                self._agent_server_thread_id(thread_id, user_id)
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                return None
            raise

    async def _run_agent_server(
        self,
        thread_id: str,
        user_id: str,
        *,
        run_input: dict[str, Any] | None = None,
        command: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self._agent_server_client()
        key = (user_id, thread_id)
        session: _GraphDebugSession | None = None

        def register_run(metadata: Mapping[str, Any]) -> None:
            nonlocal session
            session = _GraphDebugSession(run_id=str(metadata["run_id"]))
            self._graph_debug_sessions[key] = session
            self._publish_debug_event(
                session,
                "run",
                {"runId": session.run_id, "status": "running"},
            )

        try:
            async for part in client.runs.stream(
                thread_id=self._agent_server_thread_id(thread_id, user_id),
                assistant_id="mia",
                input=run_input,
                command=command,
                stream_mode=["debug"],
                stream_subgraphs=True,
                on_disconnect="continue",
                if_not_exists="create",
                on_run_created=register_run,
            ):
                if session is None and part.event == "metadata":
                    run_id = part.data.get("run_id")
                    if isinstance(run_id, str):
                        register_run({"run_id": run_id})
                self._record_debug_part(session, part.event, part.data)
            snapshot = await self._agent_server_snapshot(thread_id, user_id)
            if snapshot is None:
                raise RuntimeError("LangGraph Agent Server lost the workflow thread.")
            if session is not None:
                status = self._snapshot_status(snapshot)
                if status == "failed":
                    raise RuntimeError(
                        "LangGraph Agent Server workflow failed. Open Debug to see the failed node."
                    )
                session.status = status
                self._publish_debug_event(
                    session,
                    "run",
                    {"runId": session.run_id, "status": session.status},
                )
            return self._snapshot_values(snapshot)
        except Exception as error:
            if session is not None:
                session.status = "failed"
                self._publish_debug_event(
                    session,
                    "run",
                    {
                        "runId": session.run_id,
                        "status": "failed",
                        "errorType": type(error).__name__,
                    },
                )
            raise

    @staticmethod
    def _record_debug_part(
        session: _GraphDebugSession | None,
        event_name: str,
        data: Mapping[str, Any],
    ) -> None:
        if session is None or not event_name.startswith("debug"):
            return
        event_type = data.get("type")
        payload = data.get("payload")
        if event_type not in {"task", "task_result"} or not isinstance(payload, Mapping):
            return
        node_name = payload.get("name")
        if not isinstance(node_name, str):
            return
        namespaces = [item for item in event_name.split("|")[1:] if item]
        namespace = ":".join(namespaces)
        node_id = f"{namespace}:{node_name}" if namespace else node_name
        if event_type == "task":
            status = "running"
        elif payload.get("interrupts"):
            status = "waiting"
        else:
            status = "failed" if payload.get("error") else "completed"
        timestamp = data.get("timestamp")
        Mia._publish_debug_event(
            session,
            "node",
            {
                "nodeId": node_id,
                "nodeName": node_name,
                "namespace": namespace,
                "status": status,
                "timestamp": timestamp if isinstance(timestamp, str) else None,
            },
        )

    @staticmethod
    def _publish_debug_event(
        session: _GraphDebugSession,
        event_name: str,
        data: dict[str, Any],
    ) -> None:
        event = {"event": event_name, "data": data}
        session.events.append(event)
        for listener in tuple(session.listeners):
            listener.put_nowait(event)

    @property
    def graph_debug_enabled(self) -> bool:
        return self._use_agent_server

    async def graph_debug_stream(
        self,
        thread_id: str | None,
        *,
        user_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        if not self.graph_debug_enabled:
            raise RuntimeError("Live workflow debugging is only enabled in the local Agent Server.")
        if (
            thread_id is not None
            and self.context.catalogue.get_thread(thread_id, user_id=user_id) is None
        ):
            raise KeyError(thread_id)
        graph = await self._agent_server_client().assistants.get_graph("mia", xray=1)
        nodes = [
            {
                "id": str(node.get("id", "")),
                "name": (
                    node["data"].get("name", "")
                    if isinstance(node.get("data"), Mapping)
                    else str(node.get("data") or "")
                ),
                "type": node.get("type"),
            }
            for node in graph.get("nodes", [])
        ]
        edges = [
            {
                "source": str(edge.get("source", "")),
                "target": str(edge.get("target", "")),
                "conditional": bool(edge.get("conditional", False)),
                "label": str(edge.get("data", "")) if edge.get("data") else "",
            }
            for edge in graph.get("edges", [])
        ]
        yield {"event": "topology", "data": {"nodes": nodes, "edges": edges}}

        if thread_id is None:
            yield {"event": "run", "data": {"status": "idle"}}
            return

        session = self._graph_debug_sessions.get((user_id, thread_id))
        if session is None:
            snapshot = await self._agent_server_snapshot(thread_id, user_id)
            if snapshot is None:
                yield {"event": "run", "data": {"status": "idle"}}
                return
            status = self._snapshot_status(snapshot)
            metadata = snapshot.get("metadata", {}) if isinstance(snapshot, Mapping) else {}
            yield {
                "event": "run",
                "data": {
                    "runId": metadata.get("run_id") if isinstance(metadata, Mapping) else None,
                    "status": status,
                },
            }
            tasks = (
                snapshot.get("tasks", ())
                if isinstance(snapshot, Mapping)
                else getattr(snapshot, "tasks", ())
            )
            for task in tasks:
                task_data = task if isinstance(task, Mapping) else {}
                name = task_data.get("name")
                if not isinstance(name, str):
                    continue
                path = task_data.get("path", ())
                path_items = (
                    [item for item in path if isinstance(item, str)]
                    if isinstance(path, (list, tuple))
                    else []
                )
                namespace_items = [item for item in path_items if item != "__pregel_pull"][:-1]
                task_status = self._task_status(task_data)
                if task_status == "running" and status != "running":
                    task_status = status
                yield {
                    "event": "node",
                    "data": {
                        "nodeId": f"{':'.join(namespace_items)}:{name}"
                        if namespace_items
                        else name,
                        "nodeName": name,
                        "namespace": ":".join(namespace_items),
                        "status": task_status,
                        "timestamp": None,
                    },
                }
            if not tasks:
                next_nodes = (
                    snapshot.get("next", ())
                    if isinstance(snapshot, Mapping)
                    else getattr(snapshot, "next", ())
                )
                for node_name in next_nodes:
                    if isinstance(node_name, str):
                        yield {
                            "event": "node",
                            "data": {
                                "nodeId": node_name,
                                "nodeName": node_name,
                                "namespace": "",
                                "status": "waiting" if status != "running" else "running",
                                "timestamp": None,
                            },
                        }
            return
        listener: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        session.listeners.add(listener)
        history = list(session.events)
        try:
            yield {
                "event": "run",
                "data": {"runId": session.run_id, "status": session.status},
            }
            for event in history:
                yield event
            while session.status == "running":
                event = await listener.get()
                if event is None:
                    break
                yield event
        finally:
            session.listeners.discard(listener)

    @staticmethod
    def _snapshot_values(snapshot: Any) -> dict[str, Any]:
        values = snapshot.get("values", {}) if isinstance(snapshot, Mapping) else snapshot.values
        return dict(values) if isinstance(values, Mapping) else {}

    @staticmethod
    def _snapshot_status(snapshot: Any) -> str:
        next_nodes = (
            snapshot.get("next", ())
            if isinstance(snapshot, Mapping)
            else getattr(snapshot, "next", ())
        )
        tasks = (
            snapshot.get("tasks", ())
            if isinstance(snapshot, Mapping)
            else getattr(snapshot, "tasks", ())
        )
        if any(Mia._task_status(task) == "failed" for task in tasks):
            return "failed"
        state_interrupts = (
            snapshot.get("interrupts", ())
            if isinstance(snapshot, Mapping)
            else getattr(snapshot, "interrupts", ())
        )
        if (
            next_nodes
            or state_interrupts
            or any(Mia._task_status(task) == "waiting" for task in tasks)
        ):
            return "waiting"
        return "completed"

    @staticmethod
    def _task_status(task: Any) -> str:
        error = task.get("error") if isinstance(task, Mapping) else getattr(task, "error", None)
        interrupts = (
            task.get("interrupts", ())
            if isinstance(task, Mapping)
            else getattr(task, "interrupts", ())
        )
        if error:
            return "failed"
        if interrupts:
            return "waiting"
        return "running"

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

    def _record_failure(
        self,
        thread_id: str,
        error: Exception,
        *,
        user_id: str,
        run_id: str | None = None,
    ) -> None:
        run = (
            self.context.catalogue.get_run(run_id)
            if run_id is not None
            else self._latest_active_run(thread_id, user_id=user_id)
        )
        if run is None or not self.context.catalogue.run_is_current_generation(run.id):
            return
        if run.status not in {RunStatus.RUNNING, RunStatus.AWAITING_HUMAN}:
            return
        if self.context.catalogue.get_thread(run.thread_id, user_id=user_id) is None:
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
        run_id: str | None = None,
    ) -> None:
        run = (
            self.context.catalogue.get_run(run_id)
            if run_id is not None
            else self._latest_active_run(thread_id, user_id=user_id)
        )
        if run is None or not self.context.catalogue.run_is_current_generation(run.id):
            return
        if run.status not in {RunStatus.RUNNING, RunStatus.AWAITING_HUMAN}:
            return
        if self.context.catalogue.get_thread(run.thread_id, user_id=user_id) is None:
            return
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
        tasks = snapshot.get("tasks", ()) if isinstance(snapshot, Mapping) else snapshot.tasks
        for task in tasks:
            interrupts = (
                task.get("interrupts", ()) if isinstance(task, Mapping) else task.interrupts
            )
            if interrupts:
                return cast(object, interrupts[0])
        return None

    def _config(self, thread_id: str, user_id: str) -> dict[str, dict[str, str]]:
        # A new workflow generation creates a clean checkpoint namespace inside the same visible chat.
        thread = self.context.catalogue.get_thread(thread_id, user_id=user_id)
        generation = thread.workflow_generation if thread is not None else 0
        return {
            "configurable": {
                "thread_id": f"{user_id}:{thread_id}:generation:{generation}"
            }
        }

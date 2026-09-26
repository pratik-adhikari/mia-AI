"""Small application façade over MIA's durable LangGraph workflow."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
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
from mia_dpp.agents.conversation import (
    ConversationAction,
    PydanticConversationSupervisor,
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
from mia_dpp.persistence.catalogue import LOCAL_USER_ID, ActiveProductRunExists
from mia_dpp.persistence.workspace import WorkspaceView
from mia_dpp.orchestration.base import Orchestrator
from mia_dpp.orchestration.graph.orchestrator import GraphOrchestrator
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
from mia_dpp.tools.search import SearchProvider, SearchUnavailableError
from mia_dpp.tools.web.models import PageLoadError
from mia_dpp.tools.web.tool import WebExtractionTool
from mia_dpp.workflow.identity import direct_product_url
from mia_dpp.workflow.state import reset_product_state


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
        eclass_provider: EclassPropertyProvider | None = None,
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
        if self.settings.jev_mapping_enabled and not self.settings.jev_shadow_enabled:
            raise ValueError("MIA_JEV_MAPPING_ENABLED requires MIA_JEV_SHADOW_ENABLED")
        if self.settings.jev_shadow_enabled:
            if self.settings.openrouter_api_key is None:
                raise ValueError("MIA_JEV_SHADOW_ENABLED requires OPENROUTER_API_KEY")
            jev_decider = OpenRouterJevClient(
                api_key=self.settings.openrouter_api_key.get_secret_value(),
                model=self.settings.jev_model,
                max_concurrency=self.settings.jev_max_concurrency,
            )
        if self.settings.semantic_promotion_enabled and not self.settings.eclass_shadow_enabled:
            raise ValueError("MIA_SEMANTIC_PROMOTION_ENABLED requires MIA_ECLASS_SHADOW_ENABLED")

        resolved_eclass_provider = eclass_provider
        if self.settings.eclass_shadow_enabled:
            if jev_decider is None:
                raise ValueError("MIA_ECLASS_SHADOW_ENABLED requires MIA_JEV_SHADOW_ENABLED")
            if resolved_eclass_provider is None:
                if self.settings.eclass_provider_mode == "local":
                    if not self.settings.eclass_xml_dictionary_zips:
                        raise ValueError(
                            "MIA_ECLASS_PROVIDER=local requires MIA_ECLASS_XML_DICTIONARY_ZIPS"
                        )
                    if self.settings.eclass_certificate_file is not None:
                        raise ValueError(
                            "Configure local ECLASS XML ZIPs or an ECLASS certificate, not both"
                        )
                    dictionary_zips = tuple(
                        Path(item.strip())
                        for item in self.settings.eclass_xml_dictionary_zips.split(",")
                        if item.strip()
                    )
                    resolved_eclass_provider = EclassXmlZipProvider(
                        dictionary_zips,
                        language=self.settings.eclass_xml_language,
                    )
                elif self.settings.eclass_xml_dictionary_zips:
                    raise ValueError(
                        "Local ECLASS XML ZIPs are configured but "
                        "MIA_ECLASS_PROVIDER is not set to 'local'"
                    )
                elif self.settings.eclass_certificate_file is None:
                    raise ValueError(
                        "MIA_ECLASS_SHADOW_ENABLED requires local ECLASS XML ZIPs or "
                        "MIA_ECLASS_CERTIFICATE_FILE"
                    )
                else:
                    resolved_eclass_provider = EclassJsonV2Provider(
                        certificate_file=self.settings.eclass_certificate_file,
                        key_file=self.settings.eclass_key_file,
                        base_url=self.settings.eclass_json_base_url,
                        search_parameter=self.settings.eclass_search_parameter,
                    )

        discovery = PydanticDiscoveryAgent(agent_model, search) if agent_model is not None else None
        research = (
            PydanticResearchAgent(agent_model, search)
            if agent_model is not None
            else DeterministicResearchAgent(search)
        )
        self.context = ServiceContainer(
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
            jev_mapping_enabled=self.settings.jev_mapping_enabled,
            jev_routing_max_concurrency=self.settings.jev_max_concurrency,
            jev_decision_policy=DecisionPolicySettings(
                auto_min_selected_probability=(self.settings.jev_auto_min_selected_probability),
                auto_min_margin=self.settings.jev_auto_min_margin,
                auto_max_runner_up_ratio=self.settings.jev_auto_max_runner_up_ratio,
                auto_max_normalized_entropy=self.settings.jev_auto_max_entropy,
                optional_min_selected_probability=(
                    self.settings.jev_optional_min_selected_probability
                ),
                optional_min_margin=self.settings.jev_optional_min_margin,
                optional_max_runner_up_ratio=(self.settings.jev_optional_max_runner_up_ratio),
                optional_max_normalized_entropy=(self.settings.jev_optional_max_entropy),
            ),
            jev_grouping_max_groups=self.settings.jev_grouping_max_groups,
            eclass_shadow_enabled=self.settings.eclass_shadow_enabled,
            eclass_provider=resolved_eclass_provider,
            eclass_candidate_limit=self.settings.eclass_candidate_limit,
            semantic_promotion_enabled=self.settings.semantic_promotion_enabled,
        )
        self.store = WorkspaceView(catalogue, artifacts)
        self.query = ProductQueryService(catalogue, artifacts)
        self.conversation = (
            PydanticConversationSupervisor(agent_model, self.query)
            if agent_model is not None
            else None
        )
        self.deep_research = DeepResearchService(
            catalogue=self.context.catalogue,
            artifacts=self.context.artifacts,
            templates=self.context.templates,
            web_tool=self.context.web_tool,
            mapping_review=self.context.mapping_review,
            semantic_mapper=self.context.semantic_mapper,
            jev_decider=self.context.jev_decider,
            jev_mapping_enabled=self.context.jev_mapping_enabled,
            jev_routing_scopes=self.context.jev_routing_scopes,
            jev_routing_max_concurrency=self.context.jev_routing_max_concurrency,
            jev_decision_policy=self.context.jev_decision_policy,
        )
        self._response_view = AgentResponseView(self.context, self.store)
        self.orchestrator: Orchestrator = GraphOrchestrator(self.settings, self.context)

    @property
    def configured(self) -> bool:
        return self.context.semantic_mapper is not None

    async def reconcile_local_stale_runs(self) -> tuple[ProductRun, ...]:
        """Retire interrupted local runs that have no live executor or review checkpoint."""

        if not self.settings.local_mode:
            return ()

        async def inspect(run: ProductRun, user_id: str) -> ProductRun | None:
            try:
                allow_live_lease = False
                if run.status is RunStatus.RUNNING:
                    if self.context.catalogue.run_lease_is_live(run):
                        if not self.orchestrator.external_execution_enabled or await self._orchestrator_execution_is_active(
                            run, user_id=user_id
                        ):
                            return None
                        allow_live_lease = True
                        reason = (
                            "No active execution remained after restart; saved product work "
                            "remains available."
                        )
                    else:
                        reason = (
                            "Execution stopped and its lease expired; saved product work "
                            "remains available."
                        )
                elif run.status is RunStatus.AWAITING_HUMAN:
                    if await self._has_review_checkpoint(run, user_id=user_id):
                        return None
                    reason = (
                        "The pending review checkpoint is unavailable; saved product work "
                        "remains available."
                    )
                else:
                    return None
                return self.context.catalogue.interrupt_unresumable_run(
                    run, reason=reason, allow_live_lease=allow_live_lease
                )
            except Exception:
                logging.exception("Could not inspect run %s during local recovery", run.id)
                return None

        results = [
            await inspect(run, user_id)
            for run, user_id in self.context.catalogue.list_active_runs_with_owners()
        ]
        return tuple(run for run in results if run is not None)

    async def _orchestrator_execution_is_active(
        self,
        run: ProductRun,
        *,
        user_id: str,
    ) -> bool:
        return await self.orchestrator.execution_is_active(run.thread_id, user_id)

    async def _has_review_checkpoint(self, run: ProductRun, *, user_id: str) -> bool:
        snapshot = await self.orchestrator.snapshot(run.thread_id, user_id)
        return snapshot.values.get("run_id") == run.id and snapshot.interrupted

    async def message(
        self,
        request: AgentRequest,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> AgentResponse:
        requested_thread_id = request.thread_id
        provisional_thread_id = requested_thread_id or f"thread-{uuid.uuid4().hex}"
        provisional_existed = (
            self.context.catalogue.get_thread(
                provisional_thread_id,
                user_id=user_id,
            )
            is not None
        )
        self.context.catalogue.get_or_create_thread(
            provisional_thread_id,
            user_id,
            title=request.message[:120],
        )

        conversation = getattr(self, "conversation", None)
        if conversation is not None and not request.refresh_requested:
            recent_messages = tuple(
                {
                    "role": item.role.value,
                    "content": item.content,
                }
                for item in self.context.catalogue.list_messages(
                    provisional_thread_id,
                    user_id=user_id,
                )[-12:]
            )
            turn = await conversation.run(
                request.message,
                thread_id=provisional_thread_id,
                user_id=user_id,
                recent_messages=recent_messages,
            )
            if turn.action is ConversationAction.REPLY:
                message = self.context.catalogue.add_message(
                    provisional_thread_id,
                    MessageRole.USER,
                    request.message,
                    user_id=user_id,
                )
                self._assign_message_to_latest_run(
                    message.id,
                    provisional_thread_id,
                    user_id=user_id,
                )
                status_view = self.query.work_status(
                    provisional_thread_id,
                    user_id=user_id,
                )
                response = AgentResponse(
                    thread_id=provisional_thread_id,
                    reply=turn.reply,
                    status=self._conversation_status(status_view.run_status),
                    decision_summary=turn.decision_summary,
                    trace_events=self.store.list_events(
                        provisional_thread_id,
                        user_id=user_id,
                    )[-12:],
                    artifact_count=len(
                        self.store.list_artifacts(
                            provisional_thread_id,
                            user_id=user_id,
                        )
                    ),
                )
                self._record_assistant(response, user_id=user_id)
                return response

            if turn.action is ConversationAction.RETRY_WORK:
                message = self.context.catalogue.add_message(
                    provisional_thread_id,
                    MessageRole.USER,
                    request.message,
                    user_id=user_id,
                )
                return await self.retry_work(
                    provisional_thread_id,
                    user_id=user_id,
                    message_id=message.id,
                )

        active_product_run: ProductRun | None = None
        redirected_to_active_thread = False
        direct_url = direct_product_url(request.message)
        thread_id = provisional_thread_id
        if direct_url is not None:
            product, _ = self.context.catalogue.get_or_create_product(
                direct_url,
                user_id=user_id,
            )
            active = self.context.catalogue.latest_active_run(
                product.id,
                user_id=user_id,
            )
            if active is not None:
                active_product_run = active
                redirected_to_active_thread = active.thread_id != provisional_thread_id
                thread_id = active.thread_id

        if thread_id != provisional_thread_id and not provisional_existed:
            self.context.catalogue.delete_thread(
                provisional_thread_id,
                user_id=user_id,
            )

        thread_exists = self.context.catalogue.get_thread(thread_id, user_id=user_id) is not None
        self.context.catalogue.get_or_create_thread(
            thread_id,
            user_id,
            title=request.message[:120],
        )
        message = self.context.catalogue.add_message(
            thread_id,
            MessageRole.USER,
            request.message,
            user_id=user_id,
        )

        snapshot = await self.orchestrator.snapshot(
            thread_id,
            user_id,
            create_if_missing=not thread_exists,
        )
        trace_offset = len(self.store.list_events(thread_id, user_id=user_id))
        values = dict(snapshot.values)

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

        if snapshot.interrupted:
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
            result = await self.orchestrator.run(thread_id, user_id, update)
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
                    user_message=(
                        "Refresh sources requested while previous product work was running."
                    ),
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
        snapshot = await self.orchestrator.snapshot(thread_id, user_id)
        values = dict(snapshot.values)
        latest_run = self._latest_run(thread_id, user_id=user_id)
        if (
            latest_run is not None
            and latest_run.status is RunStatus.INCOMPLETE
            and (not values or values.get("run_id") in {None, latest_run.id})
        ):
            values = dict(values)
            values["thread_id"] = thread_id
            values["user_id"] = user_id
            values.setdefault("run_id", latest_run.id)
            values.setdefault("product_id", latest_run.product_id)
            values.setdefault(
                "reply", "This workflow was interrupted. Durable product work remains available."
            )
            values.setdefault("status", AgentStatus.FAILED.value)
        if not values:
            return AgentResponse(
                thread_id=thread_id,
                reply="",
                status=AgentStatus.AWAITING_INPUT,
                decision_summary="Conversation exists but has no active workflow state.",
            )
        values = dict(values)
        values["thread_id"] = thread_id
        values["user_id"] = user_id
        return self._response_view.build(values, trace_offset=0)

    async def close(self) -> None:
        await self.orchestrator.close()

    async def run_deep_research(self, job_id: str, *, user_id: str) -> BackgroundJob:
        """Run one durable worker invocation against catalogue-owned job state."""

        return await self.deep_research.run(job_id, user_id=user_id)

    async def retry_work(
        self,
        thread_id: str,
        *,
        user_id: str = LOCAL_USER_ID,
        message_id: str | None = None,
    ) -> AgentResponse:
        """Explicitly supersede a running/failed attempt with a new fenced generation."""

        thread = self.context.catalogue.get_thread(thread_id, user_id=user_id)
        if thread is None:
            raise KeyError(thread_id)
        run = self._latest_run(thread_id, user_id=user_id)
        if run is None:
            raise ValueError("this conversation has no product work to retry")
        if run.status not in {
            RunStatus.RUNNING,
            RunStatus.INCOMPLETE,
            RunStatus.FAILED,
        }:
            raise ValueError(f"run {run.id} is not retryable from status {run.status.value}")
        product = self.context.catalogue.get_product(run.product_id, user_id=user_id)
        if product is None:
            raise KeyError(run.product_id)

        retry_message = "Retry the interrupted product workflow."
        if message_id is None:
            user_message = self.context.catalogue.add_message(
                thread_id,
                MessageRole.USER,
                retry_message,
                run_id=run.id,
                user_id=user_id,
            )
            message_id = user_message.id
        return await self._restart_product_work_in_same_thread(
            thread_id=thread_id,
            user_id=user_id,
            active_run=run,
            product_url=product.canonical_url,
            user_message=retry_message,
            refresh_requested=False,
            previous_values={},
            trace_offset=len(self.store.list_events(thread_id, user_id=user_id)),
            message_id=message_id,
            reason="Recovered product work from the latest durable snapshot after failure.",
            allow_terminal=True,
        )

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
            snapshot = await self.orchestrator.snapshot(thread_id, user_id)
            failing_run_id = (
                str(snapshot.values["run_id"]) if snapshot.values.get("run_id") else None
            )
            result = await self.orchestrator.resume(thread_id, user_id, payload)
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
            require_expired_lease=(active_run.status is RunStatus.RUNNING and not allow_terminal),
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
                (durable.source_generation + 1 if refresh_requested else durable.source_generation)
                if durable is not None
                else 1
            ),
            "refresh_requested": refresh_requested,
            "reuse_mode": ("refresh_sources" if refresh_requested else "continue_saved_work"),
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
            result = await self.orchestrator.run(thread_id, user_id, update)
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

    @property
    def graph_debug_enabled(self) -> bool:
        return self.orchestrator.debug_enabled

    async def graph_debug_stream(
        self,
        thread_id: str | None,
        *,
        user_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        if (
            thread_id is not None
            and self.context.catalogue.get_thread(thread_id, user_id=user_id) is None
        ):
            raise KeyError(thread_id)
        async for event in self.orchestrator.debug_stream(thread_id, user_id=user_id):
            yield event

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
        retryable = self._retryable_failure(error)
        self.context.catalogue.add_event(
            run.id,
            "workflow.retryable_failure" if retryable else "workflow.failed",
            (
                "Workflow execution stopped on a retryable external dependency failure."
                if retryable
                else "Workflow execution failed."
            ),
            metadata={
                "error": detail,
                "errorType": type(error).__name__,
                "retryable": retryable,
            },
        )
        self.context.catalogue.finish_run(
            run.id,
            RunStatus.INCOMPLETE if retryable else RunStatus.FAILED,
            error=detail,
        )

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

    @staticmethod
    def _retryable_failure(error: Exception) -> bool:
        return isinstance(
            error,
            (
                httpx.HTTPError,
                SearchUnavailableError,
                PageLoadError,
                TimeoutError,
                ConnectionError,
            ),
        )

    @staticmethod
    def _conversation_status(run_status: RunStatus | None) -> AgentStatus:
        if run_status is RunStatus.AWAITING_HUMAN:
            return AgentStatus.AWAITING_REVIEW
        if run_status is RunStatus.RUNNING:
            return AgentStatus.RUNNING
        if run_status in {RunStatus.COMPLETED, RunStatus.REUSED}:
            return AgentStatus.COMPLETED
        if run_status is RunStatus.FAILED:
            return AgentStatus.FAILED
        return AgentStatus.AWAITING_INPUT

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

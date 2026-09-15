"""MIA's composition root and complete autonomous application behavior."""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.agent.dependencies import MiaDependencies
from mia_dpp.agent.models import (
    AgentRequest,
    AgentResponse,
    AgentReviewRequest,
    AgentRunOutput,
    AgentStatus,
    AgentValueRequest,
    HumanRequestKind,
    MiaState,
    ProductStatus,
    ProductWork,
    TraceStatus,
)
from mia_dpp.agent.prompts import AGENT_INSTRUCTIONS, DPP_CREATION_SKILL
from mia_dpp.agent.tools import AGENT_TOOLS
from mia_dpp.config import Settings
from mia_dpp.integrations.crawl4ai import Crawl4AIPageLoader
from mia_dpp.integrations.ddgs import DdgsSearchProvider
from mia_dpp.store import ArtifactKind, SessionSnapshot, Store
from mia_dpp.tools.mapping.review import MappingReviewService
from mia_dpp.tools.search import SearchProvider
from mia_dpp.tools.web.schema import WebSchemaStore
from mia_dpp.tools.web.tool import WebExtractionTool


class Mia:
    """Compose MIA and own its autonomous, persisted application behavior.

    FastAPI calls the small public message/review/value surface. PydanticAI
    selects and repeats reusable tools; this class persists trusted sessions and
    applies human results without exposing that authority to the model.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        model: Model | None = None,
        search_provider: SearchProvider | None = None,
        web_tool: WebExtractionTool | None = None,
    ) -> None:
        """Connect concrete capabilities, PydanticAI, and session persistence.

        Production uses configured OpenRouter, DDGS, and Crawl4AI implementations.
        Tests may inject a model, search provider, or web capability while running
        the exact same application behavior.
        """

        self.settings = settings or Settings()
        self.templates = OfficialTemplateRepository(self.settings.standards_root)

        search = search_provider or DdgsSearchProvider()
        api_key = (
            self.settings.openrouter_api_key.get_secret_value()
            if self.settings.openrouter_api_key is not None
            else None
        )
        self.web_tool = web_tool or WebExtractionTool(
            loader=Crawl4AIPageLoader(
                schema_model=f"openrouter/{self.settings.agent_model}",
                api_key=api_key,
            ),
            schemas=WebSchemaStore(self.settings.web_schema_root),
        )
        self.store = Store(
            self.settings.thread_store_path,
            artifact_root=self.settings.workspace_root,
        )

        self._search = search
        self._mapping_review = MappingReviewService(self.templates)
        agent_model = model
        if agent_model is None and self.settings.openrouter_api_key is not None:
            agent_model = OpenRouterModel(
                self.settings.agent_model,
                provider=OpenRouterProvider(
                    api_key=api_key,
                    app_url="https://mia-dpp.vercel.app",
                    app_title="MIA Digital Product Passport",
                ),
            )
        self._agent: Agent[MiaDependencies, AgentRunOutput | DeferredToolRequests] | None = None
        if agent_model is not None:
            skill = Capability[MiaDependencies](
                id="dpp-creation",
                description="How MIA approaches evidence-backed DPP and AAS creation.",
                instructions=DPP_CREATION_SKILL,
            )
            self._agent = Agent[MiaDependencies, AgentRunOutput](
                agent_model,
                name="mia-agent",
                deps_type=MiaDependencies,
                output_type=[AgentRunOutput, DeferredToolRequests],  # type: ignore[list-item]
                instructions=AGENT_INSTRUCTIONS,
                tools=AGENT_TOOLS,
                capabilities=[skill],
                retries=2,
            )

    @property
    def configured(self) -> bool:
        """Return whether an autonomous model is available for agent turns."""

        return self._agent is not None

    async def message(self, request: AgentRequest) -> AgentResponse:
        """Load one trusted session and run an autonomous PydanticAI turn."""

        thread_id = request.thread_id or f"thread-{uuid.uuid4().hex}"
        snapshot = self.store.load(thread_id)
        resolved = self.store.resolved(thread_id)
        if snapshot is not None and resolved:
            return await self._execute(
                "Continue after the trusted human action.",
                snapshot.state,
                snapshot.history,
                trace_offset=self.store.event_count(thread_id),
                deferred_results=DeferredToolResults(
                    calls={call.call_id: call.result for call in resolved}
                ),
                completed_call_ids=tuple(call.call_id for call in resolved),
            )
        if snapshot is not None and self.store.pending(thread_id):
            return self._response(
                snapshot.state,
                reply="MIA is waiting for the requested human input before continuing.",
                decision_summary="A trusted human action is required.",
                trace_offset=self.store.event_count(thread_id),
            )
        state = snapshot.state if snapshot is not None else MiaState(thread_id=thread_id)
        history = snapshot.history if snapshot is not None else []
        trace_offset = self.store.event_count(thread_id)
        if not state.user_goal:
            state.user_goal = request.message
        return await self._execute(request.message, state, history, trace_offset=trace_offset)

    async def review(self, request: AgentReviewRequest) -> AgentResponse:
        """Apply trusted mapping decisions and resume the deferred agent call."""

        return await self._resume(
            request.thread_id,
            HumanRequestKind.MAPPING_REVIEW,
            request.model_dump(mode="json"),
        )

    async def provide_value(self, request: AgentValueRequest) -> AgentResponse:
        """Apply trusted human evidence and resume the deferred agent call."""

        return await self._resume(
            request.thread_id,
            HumanRequestKind.REQUIREMENT_VALUE,
            request.model_dump(mode="json"),
        )

    async def _resume(
        self,
        thread_id: str,
        expected_kind: HumanRequestKind,
        payload: dict[str, Any],
    ) -> AgentResponse:
        snapshot = self.store.load(thread_id)
        if snapshot is None:
            raise ValueError("unknown session")
        matching = [
            call for call in self.store.pending(thread_id) if call.request.kind is expected_kind
        ]
        if len(matching) != 1:
            raise ValueError("exactly one matching human action must be pending")
        call = matching[0]
        state = snapshot.state
        trace_offset = self.store.event_count(thread_id)
        if expected_kind is HumanRequestKind.MAPPING_REVIEW:
            self._apply_reviews(state, payload)
        else:
            self._apply_human_value(state, payload)
        state.pending_human_request = None
        self.store.add_event(
            state.thread_id,
            "human.input_received",
            "Trusted human input was applied to the paused workflow.",
            product_id=call.request.product_id,
        )
        deferred_results = DeferredToolResults(
            calls={
                call.call_id: {
                    "outcome": "human_input_applied",
                    "summary": "The trusted human action was validated and applied.",
                }
            }
        )
        self.store.resolve(
            SessionSnapshot(
                state=state,
                history=snapshot.history,
                reply="Trusted human input was accepted; MIA can continue.",
                decision_summary="Resume the deferred agent action.",
            ),
            call.call_id,
            expected_kind=expected_kind,
            result=payload,
        )
        return await self._execute(
            "Continue after the trusted human action.",
            state,
            snapshot.history,
            trace_offset=trace_offset,
            deferred_results=deferred_results,
            completed_call_ids=(call.call_id,),
        )

    async def _execute(
        self,
        message: str,
        state: MiaState,
        history: Sequence[ModelMessage],
        *,
        trace_offset: int,
        deferred_results: DeferredToolResults | None = None,
        completed_call_ids: tuple[str, ...] = (),
    ) -> AgentResponse:
        output, messages = await self._run_agent(
            message,
            state,
            history,
            deferred_results=deferred_results,
        )
        if isinstance(output, DeferredToolRequests):
            request = state.pending_human_request
            if request is None or not output.calls:
                raise ValueError("agent deferred without a trusted human request")
            reply = request.summary
            decision = "A trusted human action is required."
        else:
            reply = output.reply
            decision = output.decision_summary
        snapshot = SessionSnapshot(
            state=state,
            history=messages,
            reply=reply,
            decision_summary=decision,
        )
        request = state.pending_human_request if isinstance(output, DeferredToolRequests) else None
        deferred_calls = (
            [(call.tool_call_id, request) for call in output.calls]
            if request is not None and isinstance(output, DeferredToolRequests)
            else []
        )
        self.store.save(
            snapshot,
            deferred_calls=deferred_calls,
            completed_call_ids=completed_call_ids,
        )
        if isinstance(output, DeferredToolRequests):
            request = state.pending_human_request
            if request is None:
                raise ValueError("deferred human request disappeared before persistence")
        return self._response(
            state,
            reply=reply,
            decision_summary=decision,
            trace_offset=trace_offset,
        )

    async def _run_agent(
        self,
        message: str,
        state: MiaState,
        history: Sequence[ModelMessage],
        *,
        deferred_results: DeferredToolResults | None = None,
    ) -> tuple[AgentRunOutput | DeferredToolRequests, list[ModelMessage]]:
        """Give trusted state and reusable tools to one autonomous model turn."""

        dependencies = MiaDependencies(
            state=state,
            search=self._search,
            web_tool=self.web_tool,
            templates=self.templates,
            mapping_review=self._mapping_review,
            store=self.store,
        )
        if self._agent is None:
            state.status = AgentStatus.AWAITING_INPUT
            dependencies.add_event(
                "run.configuration_required",
                "MIA agent requires OPENROUTER_API_KEY.",
            )
            return (
                AgentRunOutput(
                    reply="Configure OPENROUTER_API_KEY to use the autonomous MIA agent.",
                    status=AgentStatus.AWAITING_INPUT,
                    decision_summary=(
                        "No model call was attempted because the server is unconfigured."
                    ),
                ),
                list(history),
            )

        dependencies.add_event(
            "run.started",
            "MIA started an autonomous decision loop.",
            status=TraceStatus.STARTED,
            input_summary=message[:200],
        )
        result = await self._agent.run(
            self._prompt_with_state(message, state),
            deps=dependencies,
            message_history=history,
            conversation_id=state.thread_id,
            deferred_tool_results=deferred_results,
        )
        output = result.output
        if isinstance(output, AgentRunOutput) and state.status is AgentStatus.RUNNING:
            state.status = output.status
        dependencies.add_event(
            "run.deferred" if isinstance(output, DeferredToolRequests) else "run.completed",
            (
                "MIA is waiting for trusted external input."
                if isinstance(output, DeferredToolRequests)
                else output.decision_summary
            ),
            metadata={
                "requestCount": result.usage.requests,
                "inputTokens": result.usage.input_tokens,
                "outputTokens": result.usage.output_tokens,
            },
        )
        return output, result.all_messages()

    @staticmethod
    def _prompt_with_state(message: str, state: MiaState) -> str:
        """Attach compact trusted job state without copying evidence into chat history."""

        compact = {
            "threadId": state.thread_id,
            "goal": state.user_goal,
            "selectedCompany": (
                state.selected_company.model_dump(mode="json") if state.selected_company else None
            ),
            "companyCandidates": [
                {"id": item.id, "name": item.name, "domain": item.domain}
                for item in state.company_candidates
            ],
            "productCandidates": [
                {"id": item.id, "name": item.name, "url": item.official_url}
                for item in state.product_candidates
            ],
            "selectedProductIds": list(state.selected_product_ids),
            "products": {
                key: {
                    "sourceCount": len(value.source_urls),
                    "sourceCandidates": [
                        {
                            "id": item.id,
                            "url": item.url,
                            "authoritative": item.authoritative_domain,
                        }
                        for item in value.source_candidates
                    ],
                    "hasEvidence": bool(value.evidence),
                    "hasMapping": value.mapping_result is not None,
                    "pendingReviews": len(value.pending_reviews),
                    "hasArtifact": value.aas_artifact_sha256 is not None,
                }
                for key, value in state.products.items()
            },
            "status": state.status,
        }
        return (
            message
            + "\n\nTrusted current MIA job state (server supplied):\n"
            + json.dumps(compact, ensure_ascii=False)
        )

    def _apply_reviews(self, state: MiaState, payload: object) -> None:
        request = AgentReviewRequest.model_validate(payload)
        work = state.products.get(request.product_id)
        if work is None or work.mapping_result is None or work.template_index is None:
            raise ValueError("unknown or unresolved product")
        package = work.knowledge_package()
        if package is None:
            raise ValueError("product evidence is unavailable")
        pending = {item.id: item for item in work.pending_reviews}
        for decision in request.decisions:
            item = pending.get(decision.review_id)
            if item is None:
                raise ValueError(f"review is not pending: {decision.review_id}")
            package, work.mapping_result, reviewed = self._mapping_review.decide(
                package,
                work.mapping_result,
                work.template_index,
                item,
                decision=decision.decision,
                thread_id=state.thread_id,
                corrected_requirement_id=decision.corrected_requirement_id,
                corrected_value=decision.corrected_value,
                comment=decision.comment,
            )
            work.product_name = package.product_name
            work.source_artifact_ids = package.source_artifact_ids
            work.evidence = package.evidence
            company = state.selected_company
            self.store.remember_mapping_review(
                reviewed.mapping,
                decision=decision.decision,
                manufacturer=company.name if company else None,
                domain=company.domain if company else None,
                product_family=work.candidate.family if work.candidate else None,
                comment=decision.comment,
            )
            artifact = self.store.write_json(
                state.thread_id,
                ArtifactKind.REVIEW,
                "mapping-review.json",
                decision.model_dump(mode="json"),
                created_by="human",
                product_id=request.product_id,
                derived_from=(reviewed.mapping.evidence_id,),
            )
            work.artifact_ids = (*work.artifact_ids, artifact.id)
            pending.pop(decision.review_id)
        work.pending_reviews = tuple(pending.values())
        work.status = ProductStatus.AWAITING_REVIEW if pending else ProductStatus.IN_PROGRESS
        state.products[request.product_id] = work
        state.status = AgentStatus.AWAITING_REVIEW if pending else AgentStatus.RUNNING

    def _apply_human_value(self, state: MiaState, payload: object) -> None:
        request = AgentValueRequest.model_validate(payload)
        work = state.products.get(request.product_id)
        if work is None or work.mapping_result is None or work.template_index is None:
            raise ValueError("unknown or unresolved product")
        package = work.knowledge_package()
        if package is None:
            raise ValueError("product evidence is unavailable")
        package, work.mapping_result = self._mapping_review.record_human_value(
            package,
            work.mapping_result,
            work.template_index,
            requirement_id=request.requirement_id,
            value=request.value,
            thread_id=state.thread_id,
        )
        work.product_name = package.product_name
        work.source_artifact_ids = package.source_artifact_ids
        work.evidence = package.evidence
        artifact = self.store.write_json(
            state.thread_id,
            ArtifactKind.REVIEW,
            "human-evidence.json",
            request.model_dump(mode="json"),
            created_by="human",
            product_id=request.product_id,
        )
        work.artifact_ids = (*work.artifact_ids, artifact.id)
        state.products[request.product_id] = work
        state.status = AgentStatus.RUNNING

    def _response(
        self,
        state: MiaState,
        *,
        reply: str,
        decision_summary: str,
        trace_offset: int,
    ) -> AgentResponse:
        current = state.products.get(state.current_product_id) if state.current_product_id else None
        trusted_reply = self._human_request_reply(state, current) or reply
        return AgentResponse(
            thread_id=state.thread_id,
            reply=trusted_reply,
            status=state.status,
            decision_summary=decision_summary,
            company_candidates=state.company_candidates,
            selected_company=state.selected_company,
            product_candidates=state.product_candidates,
            selected_product_ids=state.selected_product_ids,
            current_product=current,
            pending_human_request=state.pending_human_request,
            trace_events=self.store.list_events(state.thread_id, trace_offset),
            artifact_count=len(self.store.list_artifacts(state.thread_id)),
        )

    @staticmethod
    def _human_request_reply(state: MiaState, current: ProductWork | None) -> str | None:
        """Describe a trusted interrupt using counts from state rather than model prose."""

        request = state.pending_human_request
        if request is None:
            return None
        if request.kind is HumanRequestKind.REQUIREMENT_VALUE:
            return request.summary
        if current is None or current.mapping_result is None:
            return "Mapping proposals need review. Please approve, correct, or reject them."

        mapping = current.mapping_result
        mapping_count = len(mapping.mapped)
        review_count = len(current.pending_reviews)
        unmatched_count = len(mapping.unmatched_evidence_ids)
        return (
            "I finished processing the currently available source evidence.\n\n"
            f"- {len(current.evidence)} source facts retained\n"
            f"- {mapping_count} mapping{'s' if mapping_count != 1 else ''} accepted "
            "deterministically\n"
            f"- {review_count} mapping proposal{'s' if review_count != 1 else ''} "
            f"{'needs' if review_count == 1 else 'need'} review\n"
            f"- {unmatched_count} source facts remain unmatched"
            "\n\nPlease approve, correct, or reject the pending mapping review."
        )

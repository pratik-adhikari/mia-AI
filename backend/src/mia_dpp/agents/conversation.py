"""General conversation supervisor over durable MIA read tools."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum

from pydantic import Field
from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.models import Model

from mia_dpp.domain.base import WireModel
from mia_dpp.services.product_query import ProductQueryService


class ConversationAction(StrEnum):
    REPLY = "reply"
    WORKFLOW = "workflow"


class ConversationTurn(WireModel):
    """Typed boundary between general chat and deterministic workflow execution."""

    action: ConversationAction
    reply: str = Field(min_length=1, max_length=4000)
    decision_summary: str = Field(min_length=1, max_length=500)


@dataclass(slots=True)
class ConversationDependencies:
    query: ProductQueryService
    thread_id: str
    user_id: str


async def get_work_status(ctx: RunContext[ConversationDependencies]) -> str:
    status = ctx.deps.query.work_status(
        ctx.deps.thread_id,
        user_id=ctx.deps.user_id,
    )
    return status.model_dump_json(by_alias=True)


async def search_product_evidence(
    ctx: RunContext[ConversationDependencies],
    query: str,
) -> str:
    hits = ctx.deps.query.search_evidence(
        ctx.deps.thread_id,
        query,
        user_id=ctx.deps.user_id,
    )
    return json.dumps(
        [item.model_dump(mode="json", by_alias=True) for item in hits],
        ensure_ascii=False,
    )


async def get_recent_progress(
    ctx: RunContext[ConversationDependencies],
    limit: int = 10,
) -> str:
    events = ctx.deps.query.recent_events(
        ctx.deps.thread_id,
        user_id=ctx.deps.user_id,
        limit=limit,
    )
    return json.dumps(
        [item.model_dump(mode="json", by_alias=True) for item in events],
        ensure_ascii=False,
        default=str,
    )


_INSTRUCTIONS = """You are MIA's general conversation supervisor. Keep normal conversation
responsive even while the DPP workflow is running.

Use get_work_status for questions about progress, failures, waiting/review state, or what MIA is
doing. Use search_product_evidence before answering factual questions about the current product's
technical data. Use get_recent_progress when the user asks what happened recently or why work is
waiting.

Return action=reply for conversational questions, explanations, status questions, and product-data
questions that can be answered from the read tools. Never invent product facts, technical values,
sources, semantic identifiers, workflow progress, or completion state. If requested product data is
not yet present, say that it is not available yet.

Return action=workflow only when the user is instructing MIA to start/import/refresh product work,
select a discovery candidate, continue a paused workflow choice, or otherwise perform workflow
execution. The workflow, not this supervisor, owns extraction, research, mapping, reviews, and AAS
generation. Never directly mutate product/work state."""


class PydanticConversationSupervisor:
    """General LLM that reads durable state but never becomes the workflow."""

    def __init__(self, model: Model, query: ProductQueryService) -> None:
        self._query = query
        self._agent = Agent[
            ConversationDependencies,
            ConversationTurn,
        ](
            model,
            name="mia-conversation-supervisor",
            deps_type=ConversationDependencies,
            output_type=ConversationTurn,
            instructions=_INSTRUCTIONS,
            tools=(
                Tool(get_work_status, sequential=True),
                Tool(search_product_evidence, sequential=True),
                Tool(get_recent_progress, sequential=True),
            ),
            retries=1,
        )

    async def run(
        self,
        message: str,
        *,
        thread_id: str,
        user_id: str,
        recent_messages: tuple[dict[str, str], ...] = (),
    ) -> ConversationTurn:
        prompt = json.dumps(
            {
                "message": message,
                "recentConversation": list(recent_messages[-12:]),
            },
            ensure_ascii=False,
        )
        result = await self._agent.run(
            prompt,
            deps=ConversationDependencies(
                query=self._query,
                thread_id=thread_id,
                user_id=user_id,
            ),
        )
        return result.output

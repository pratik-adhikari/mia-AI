"""Conversation-to-product resolution before deterministic DPP processing."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

from mia_dpp.agent.models import AgentStatus
from mia_dpp.agents.discovery.models import DiscoveryState
from mia_dpp.runtime.services import ServiceContainer
from mia_dpp.workflow.state import MiaWorkflowState, reset_product_state

_URL = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)


async def discover_product(
    state: MiaWorkflowState,
    runtime: Runtime[ServiceContainer],
) -> dict[str, Any]:
    """Resolve direct URLs without a model; use the discovery agent only for ambiguity."""

    message = state.get("user_message", "")
    terminal = state.get("status") in {"completed", "reused", "failed"}
    direct = _URL.search(message)
    if direct:
        reset = reset_product_state(product_url=direct.group(0).rstrip(".,;")) if terminal else {}
        return {
            **reset,
            "product_url": direct.group(0).rstrip(".,;"),
            "product_queue": (),
            "status": "running",
            "reply": "Product URL resolved; starting evidence-backed DPP creation.",
            "decision_summary": "A direct product URL was supplied.",
        }
    if state.get("product_url") and not terminal:
        return {}
    base_state = reset_product_state() if terminal else {}
    agent = runtime.context.discovery_agent
    if agent is None:
        return {
            **base_state,
            "status": AgentStatus.AWAITING_INPUT.value,
            "reply": "Provide an exact product URL to create a DPP.",
            "decision_summary": "Product discovery requires a configured model or direct URL.",
        }
    discovery = DiscoveryState(
        company_candidates=() if terminal else state.get("company_candidates", ()),
        selected_company=None if terminal else state.get("selected_company"),
        product_candidates=() if terminal else state.get("product_candidates", ()),
        selected_product_ids=() if terminal else state.get("selected_product_ids", ()),
        product_url="" if terminal else state.get("product_url", ""),
        product_queue=() if terminal else state.get("product_queue", ()),
        status=AgentStatus(state.get("status", AgentStatus.RUNNING.value)),
    )
    turn = await agent.run(
        message,
        discovery,
        state.get("discovery_history_json", "[]"),
    )
    return {
        **base_state,
        "discovery_history_json": turn.history_json,
        "company_candidates": turn.state.company_candidates,
        "selected_company": turn.state.selected_company,
        "product_candidates": turn.state.product_candidates,
        "selected_product_ids": turn.state.selected_product_ids,
        "product_url": turn.state.product_url,
        "product_queue": turn.state.product_queue,
        "status": turn.state.status.value,
        "reply": turn.output.reply,
        "decision_summary": turn.output.decision_summary,
    }

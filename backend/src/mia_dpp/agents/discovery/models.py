"""Typed state and runtime contract for product discovery only."""

from __future__ import annotations

from typing import Protocol

from pydantic import Field

from mia_dpp.agent.models import AgentRunOutput, AgentStatus
from mia_dpp.domain.base import WireModel
from mia_dpp.domain.discovery import CompanyCandidate, ProductCandidate


class DiscoveryState(WireModel):
    company_candidates: tuple[CompanyCandidate, ...] = ()
    selected_company: CompanyCandidate | None = None
    product_candidates: tuple[ProductCandidate, ...] = ()
    selected_product_ids: tuple[str, ...] = ()
    product_url: str = ""
    product_queue: tuple[str, ...] = ()
    status: AgentStatus = AgentStatus.RUNNING


class DiscoveryTurn(WireModel):
    output: AgentRunOutput
    state: DiscoveryState
    history_json: str = Field(min_length=2)


class DiscoveryAgent(Protocol):
    async def run(
        self,
        message: str,
        state: DiscoveryState,
        history_json: str,
    ) -> DiscoveryTurn: ...

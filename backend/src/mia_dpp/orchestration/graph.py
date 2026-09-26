"""Adapter exposing the existing LangGraph workflow as an orchestrator."""

from __future__ import annotations

from typing import Any

from mia_dpp.runtime.services import ServiceContainer
from mia_dpp.workflow.graph import create_graph


class GraphOrchestrator:
    architecture_id = "graph-v1"

    def __init__(self, services: ServiceContainer) -> None:
        self._services = services

    async def build(self, *, checkpointer: Any | None = None) -> Any:
        return create_graph(checkpointer, context=self._services)

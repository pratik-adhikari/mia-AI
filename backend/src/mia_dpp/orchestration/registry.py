"""Architecture registry for selecting interchangeable orchestration engines."""

from __future__ import annotations

from collections.abc import Callable
from mia_dpp.orchestration.base import Orchestrator
from mia_dpp.orchestration.graph import GraphOrchestrator
from mia_dpp.runtime.services import ServiceContainer

OrchestratorFactory = Callable[[ServiceContainer], Orchestrator]


class ArchitectureRegistry:
    """Small explicit registry; future GUI/agentic architectures plug in here."""

    def __init__(self) -> None:
        self._factories: dict[str, OrchestratorFactory] = {
            GraphOrchestrator.architecture_id: GraphOrchestrator,
        }

    def register(self, architecture_id: str, factory: OrchestratorFactory) -> None:
        if architecture_id in self._factories:
            raise ValueError(f"architecture already registered: {architecture_id}")
        self._factories[architecture_id] = factory

    def create(self, architecture_id: str, services: ServiceContainer) -> Orchestrator:
        try:
            factory = self._factories[architecture_id]
        except KeyError as error:
            known = ", ".join(sorted(self._factories))
            raise ValueError(f"unknown architecture {architecture_id!r}; known: {known}") from error
        return factory(services)

    def available(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

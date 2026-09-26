"""LangGraph adapter over architecture-neutral run persistence."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from mia_dpp.runtime.run_context import RunContext
from mia_dpp.runtime.run_store import RunStore
from mia_dpp.runtime.services import ServiceContainer
from mia_dpp.workflow.state import MiaWorkflowState

ModelT = TypeVar("ModelT", bound=BaseModel)


class RunWorkspace(RunStore):
    """Adapt LangGraph state keys to the shared RunStore.

    Orchestration-specific conveniences stay here. Persistence, fencing, and
    run identity live in the runtime package so future orchestrators can reuse them.
    """

    def __init__(self, state: MiaWorkflowState, ctx: ServiceContainer) -> None:
        self.state = state
        self.ctx = ctx
        super().__init__(RunContext.from_mapping(state), ctx)

    def state_id(self, key: str) -> str:
        artifact_id = self.state.get(key)
        if not artifact_id:
            raise KeyError(f"missing state artifact id: {key}")
        return str(artifact_id)

    def load_state(self, key: str, model: type[ModelT]) -> ModelT:
        return self.load(self.state_id(key), model)

    def load_json(self, key: str) -> Any:
        return super().load_json(self.state_id(key))

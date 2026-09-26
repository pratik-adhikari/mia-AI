"""LangGraph adapter over the architecture-neutral run store."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel

from mia_dpp.runtime.run_store import RunContext, RunStore
from mia_dpp.runtime.services import ServiceContainer
from mia_dpp.workflow.state import MiaWorkflowState

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(slots=True)
class RunWorkspace:
    """Expose graph-state artifact keys while delegating persistence to RunStore."""

    state: MiaWorkflowState
    ctx: ServiceContainer
    _store: RunStore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._store = RunStore(RunContext.from_mapping(self.state), self.ctx)

    @property
    def product_id(self) -> str:
        return self._store.product_id

    @property
    def run_id(self) -> str:
        return self._store.run_id

    @property
    def user_id(self) -> str:
        return self._store.user_id

    def heartbeat(self) -> None:
        self._store.heartbeat()

    def state_id(self, key: str) -> str:
        artifact_id = self.state.get(key)
        if not artifact_id:
            raise KeyError(f"missing state artifact id: {key}")
        return str(artifact_id)

    def load(self, artifact_id: str, model: type[ModelT]) -> ModelT:
        return self._store.load(artifact_id, model)

    def load_state(self, key: str, model: type[ModelT]) -> ModelT:
        return self._store.load(self.state_id(key), model)

    def load_json(self, key: str) -> Any:
        return self._store.load_json(self.state_id(key))

    def put_model(self, key: str, value: BaseModel, *, derived_from: tuple[str, ...] = ()) -> str:
        return self._store.put_model(key, value, derived_from=derived_from)

    def put_json(self, key: str, value: object, *, derived_from: tuple[str, ...] = ()) -> str:
        return self._store.put_json(key, value, derived_from=derived_from)

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        derived_from: tuple[str, ...] = (),
    ) -> str:
        return self._store.put_bytes(
            key,
            data,
            content_type=content_type,
            derived_from=derived_from,
        )

    def event(
        self,
        event_type: str,
        summary: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._store.event(event_type, summary, metadata=metadata)

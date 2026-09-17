"""Run-scoped helpers that keep persistence plumbing out of graph nodes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

from mia_dpp.workflow.context import MiaContext
from mia_dpp.workflow.state import MiaWorkflowState

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(slots=True)
class RunWorkspace:
    state: MiaWorkflowState
    ctx: MiaContext

    @property
    def product_id(self) -> str:
        return self.state["product_id"]

    @property
    def run_id(self) -> str:
        return self.state["run_id"]

    def state_id(self, key: str) -> str:
        artifact_id = self.state.get(key)
        if not artifact_id:
            raise KeyError(f"missing state artifact id: {key}")
        return str(artifact_id)

    def load(self, artifact_id: str, model: type[ModelT]) -> ModelT:
        artifact = self.ctx.catalogue.get_artifact(artifact_id)
        if artifact is None:
            raise KeyError(f"unknown artifact: {artifact_id}")
        return model.model_validate_json(self.ctx.artifacts.get(artifact))

    def load_state(self, key: str, model: type[ModelT]) -> ModelT:
        return self.load(self.state_id(key), model)

    def load_json(self, key: str) -> Any:
        artifact = self.ctx.catalogue.get_artifact(self.state_id(key))
        if artifact is None:
            raise KeyError(f"unknown artifact: {self.state_id(key)}")
        return json.loads(self.ctx.artifacts.get(artifact))

    def put_model(
        self,
        key: str,
        value: BaseModel,
        *,
        derived_from: tuple[str, ...] = (),
    ) -> str:
        return self.put_bytes(
            key,
            value.model_dump_json(by_alias=True).encode(),
            content_type="application/json",
            derived_from=derived_from,
        )

    def put_json(
        self,
        key: str,
        value: object,
        *,
        derived_from: tuple[str, ...] = (),
    ) -> str:
        return self.put_bytes(
            key,
            json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode(),
            content_type="application/json",
            derived_from=derived_from,
        )

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        derived_from: tuple[str, ...] = (),
    ) -> str:
        artifact = self.ctx.artifacts.put(
            key,
            data,
            content_type=content_type,
            product_id=self.product_id,
            run_id=self.run_id,
            derived_from=derived_from,
        )
        self.ctx.catalogue.register_artifact(artifact)
        return artifact.id

    def event(
        self,
        event_type: str,
        summary: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.ctx.catalogue.add_event(self.run_id, event_type, summary, metadata=metadata)

"""Architecture-neutral run identity, artifact persistence, and event recording."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, TypeVar

from pydantic import BaseModel

from mia_dpp.runtime.services import ServiceContainer

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class RunContext:
    user_id: str
    thread_id: str
    product_id: str
    run_id: str

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "RunContext":
        return cls(
            user_id=str(values["user_id"]),
            thread_id=str(values["thread_id"]),
            product_id=str(values["product_id"]),
            run_id=str(values["run_id"]),
        )


@dataclass(slots=True)
class RunStore:
    """Persistence boundary reusable by every orchestration architecture."""

    scope: RunContext
    services: ServiceContainer

    def __post_init__(self) -> None:
        self._heartbeat()

    @property
    def product_id(self) -> str:
        return self.scope.product_id

    @property
    def run_id(self) -> str:
        return self.scope.run_id

    @property
    def user_id(self) -> str:
        return self.scope.user_id

    @property
    def thread_id(self) -> str:
        return self.scope.thread_id

    def _heartbeat(self) -> None:
        self.services.catalogue.assert_run_generation(self.run_id)
        self.services.catalogue.renew_run_lease(self.run_id)

    def heartbeat(self) -> None:
        self._heartbeat()

    def load(self, artifact_id: str, model: type[ModelT]) -> ModelT:
        artifact = self.services.catalogue.get_artifact(artifact_id, user_id=self.user_id)
        if artifact is None:
            raise KeyError(f"unknown artifact: {artifact_id}")
        return model.model_validate_json(self.services.artifacts.get(artifact))

    def load_json(self, artifact_id: str) -> Any:
        artifact = self.services.catalogue.get_artifact(artifact_id, user_id=self.user_id)
        if artifact is None:
            raise KeyError(f"unknown artifact: {artifact_id}")
        return json.loads(self.services.artifacts.get(artifact))

    def put_model(
        self,
        key: str,
        value: BaseModel,
        *,
        derived_from: tuple[str, ...] = (),
    ) -> str:
        return self.put_bytes(
            key,
            value.model_dump_json(by_alias=True, indent=2).encode(),
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
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str).encode(),
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
        self._heartbeat()
        artifact = self.services.artifacts.put(
            key,
            data,
            content_type=content_type,
            product_id=self.product_id,
            run_id=self.run_id,
            derived_from=derived_from,
        )
        self.services.catalogue.register_artifact(artifact)
        return artifact.id

    def event(
        self,
        event_type: str,
        summary: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._heartbeat()
        self.services.catalogue.add_event(self.run_id, event_type, summary, metadata=metadata)

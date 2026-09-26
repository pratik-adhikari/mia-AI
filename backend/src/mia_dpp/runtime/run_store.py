"""Architecture-neutral active-run persistence and event helpers."""

from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel

from mia_dpp.runtime.run_catalogue import RunCatalogue
from mia_dpp.runtime.run_context import RunContext
from mia_dpp.storage.base import ArtifactStore

ModelT = TypeVar("ModelT", bound=BaseModel)


class RunStore:
    """Persist artifacts and events for one active, fully identified execution.

    Construction validates that run_id, product_id, thread_id, and user_id
    identify the same durable execution, then renews that execution lease.
    This is intentionally an active-executor abstraction, not a read-only
    historical run reader.
    """

    def __init__(
        self,
        context: RunContext,
        catalogue: RunCatalogue,
        artifacts: ArtifactStore,
    ) -> None:
        self.context = context
        self._catalogue = catalogue
        self._artifacts = artifacts
        self._validate_context_binding()
        self._heartbeat()

    def _validate_context_binding(self) -> None:
        """Bind all context identifiers to the durable run before lease mutation."""

        run = self._catalogue.get_run(self.run_id)
        if run is None:
            raise KeyError(f"unknown run: {self.run_id}")
        if run.product_id != self.product_id:
            raise ValueError(
                f"run {self.run_id} belongs to product {run.product_id}, "
                f"not {self.product_id}"
            )
        if run.thread_id != self.thread_id:
            raise ValueError(
                f"run {self.run_id} belongs to thread {run.thread_id}, "
                f"not {self.thread_id}"
            )
        if self._catalogue.get_thread(self.thread_id, user_id=self.user_id) is None:
            raise PermissionError(
                f"thread {self.thread_id} does not belong to user {self.user_id}"
            )

    def _heartbeat(self) -> None:
        """Fence stale workers and extend the lease of the current running generation."""

        self._catalogue.assert_run_generation(self.run_id)
        self._catalogue.renew_run_lease(self.run_id)

    def heartbeat(self) -> None:
        """Renew the active run lease while retaining generation fencing."""

        self._heartbeat()

    @property
    def product_id(self) -> str:
        return self.context.product_id

    @property
    def run_id(self) -> str:
        return self.context.run_id

    @property
    def user_id(self) -> str:
        return self.context.user_id

    @property
    def thread_id(self) -> str:
        return self.context.thread_id

    def load(self, artifact_id: str, model: type[ModelT]) -> ModelT:
        artifact = self._catalogue.get_artifact(artifact_id, user_id=self.user_id)
        if artifact is None:
            raise KeyError(f"unknown artifact: {artifact_id}")
        return model.model_validate_json(self._artifacts.get(artifact))

    def load_json(self, artifact_id: str) -> Any:
        """Load JSON by durable artifact ID."""

        artifact = self._catalogue.get_artifact(artifact_id, user_id=self.user_id)
        if artifact is None:
            raise KeyError(f"unknown artifact: {artifact_id}")
        return json.loads(self._artifacts.get(artifact))

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
        artifact = self._artifacts.put(
            key,
            data,
            content_type=content_type,
            product_id=self.product_id,
            run_id=self.run_id,
            derived_from=derived_from,
        )
        self._catalogue.register_artifact(artifact)
        return artifact.id

    def event(
        self,
        event_type: str,
        summary: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._heartbeat()
        self._catalogue.add_event(self.run_id, event_type, summary, metadata=metadata)

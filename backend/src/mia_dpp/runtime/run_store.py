"""Architecture-neutral run persistence and event helpers."""

from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel

from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.runtime.run_context import RunContext
from mia_dpp.storage.base import ArtifactStore

ModelT = TypeVar("ModelT", bound=BaseModel)


class RunStore:
    """Persist artifacts and events for one fully identified run.

    The store intentionally depends only on durable catalogue metadata and
    artifact-byte storage. It must not become a service locator for semantic,
    search, agent, or orchestration dependencies.
    """

    def __init__(
        self,
        context: RunContext,
        catalogue: ProductCatalogue,
        artifacts: ArtifactStore,
    ) -> None:
        self.context = context
        self._catalogue = catalogue
        self._artifacts = artifacts
        self._heartbeat()

    def _heartbeat(self) -> None:
        """Fence stale workers and extend the lease of the current running generation."""

        self._catalogue.assert_run_generation(self.run_id)
        self._catalogue.renew_run_lease(self.run_id)

    def heartbeat(self) -> None:
        """Renew the run lease during a long wait while retaining generation fencing."""

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
        """Load JSON by durable artifact ID.

        This method has the same meaning for every RunStore subtype. Adapters
        that translate orchestration state keys must expose a differently named
        helper instead of overriding this contract.
        """

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
            # Persisted JSON is an audit artifact, so favor readability over byte size.
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
            # Stable ordering makes generated manifests easy to diff and reproduce.
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

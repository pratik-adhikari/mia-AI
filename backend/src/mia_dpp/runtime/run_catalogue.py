"""Narrow durable catalogue contract required by active run persistence."""

from __future__ import annotations

from typing import Any, Protocol

from mia_dpp.domain.product import ProductRun, RunEvent, ThreadRecord
from mia_dpp.storage.models import StoredArtifact


class RunCatalogue(Protocol):
    """Only catalogue operations needed by RunStore.

    Keeping this protocol narrow makes the runtime persistence boundary
    machine-checkable rather than relying on comments around ProductCatalogue.
    """

    def get_run(self, run_id: str) -> ProductRun | None: ...

    def get_thread(self, thread_id: str, *, user_id: str) -> ThreadRecord | None: ...

    def assert_run_generation(self, run_id: str) -> ProductRun: ...

    def renew_run_lease(self, run_id: str) -> ProductRun: ...

    def get_artifact(
        self,
        artifact_id: str,
        *,
        user_id: str | None = None,
    ) -> StoredArtifact | None: ...

    def register_artifact(self, artifact: StoredArtifact) -> StoredArtifact: ...

    def add_event(
        self,
        run_id: str,
        event_type: str,
        summary: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> RunEvent: ...

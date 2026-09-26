"""Architecture-neutral orchestration contract used by the application layer."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class OrchestrationRunSeed:
    """Durable product-work facts used when starting a replacement execution."""

    product_url: str
    product_id: str
    run_id: str
    workflow_generation: int
    source_generation: int
    reuse_mode: str
    reuse_prior_work: bool
    seeded_from_run_id: str
    evidence_artifact_id: str
    reviewed_mapping_artifact_id: str
    product_snapshot_version: int
    discovery_history_json: str = "[]"
    target_submodels: tuple[str, ...] = ("digital_nameplate", "technical_data")
    max_research_attempts: int = 2


@dataclass(frozen=True, slots=True)
class OrchestrationRunRequest:
    """Architecture-neutral request to start or continue product processing."""

    thread_id: str
    user_id: str
    user_message: str
    refresh_requested: bool = False
    initialize: bool = False
    seed: OrchestrationRunSeed | None = None


@dataclass(frozen=True, slots=True)
class OrchestrationSnapshot:
    """Application-facing view of one orchestrator checkpoint/execution state."""

    values: dict[str, Any]
    status: str
    interrupted: bool = False


class Orchestrator(Protocol):
    """Replaceable execution architecture used by the MIA application."""

    @property
    def external_execution_enabled(self) -> bool: ...

    @property
    def debug_enabled(self) -> bool: ...

    async def snapshot(
        self,
        thread_id: str,
        user_id: str,
        *,
        create_if_missing: bool = False,
    ) -> OrchestrationSnapshot: ...

    async def run(
        self,
        thread_id: str,
        user_id: str,
        request: OrchestrationRunRequest,
    ) -> dict[str, Any]: ...

    async def resume(
        self,
        thread_id: str,
        user_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def execution_is_active(self, thread_id: str, user_id: str) -> bool: ...

    async def debug_stream(
        self,
        thread_id: str | None,
        *,
        user_id: str,
    ) -> AsyncIterator[dict[str, Any]]: ...

    async def close(self) -> None: ...

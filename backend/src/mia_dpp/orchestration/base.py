"""Architecture-neutral orchestration contract used by the application layer."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol


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
        run_input: dict[str, Any],
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

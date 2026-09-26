"""Stable orchestration boundary used by application code."""

from __future__ import annotations

from typing import Any, Protocol


class Orchestrator(Protocol):
    """Execution engine for one architecture.

    Implementations may be fixed graphs, agentic controllers, GUI-composed
    pipelines, or evaluation/replay engines. They must use shared capabilities
    rather than own business logic.
    """

    @property
    def architecture_id(self) -> str: ...

    async def build(self, *, checkpointer: Any | None = None) -> Any: ...

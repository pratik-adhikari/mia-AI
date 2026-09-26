"""Architecture-neutral identity of one durable product-processing run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class RunContext:
    """Stable run identity shared by graph, agentic, CLI, and test runtimes."""

    user_id: str
    thread_id: str
    product_id: str
    run_id: str

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "RunContext":
        """Build a run context without depending on an orchestrator-specific state type."""

        return cls(
            user_id=_required_string(values, "user_id"),
            thread_id=_required_string(values, "thread_id"),
            product_id=_required_string(values, "product_id"),
            run_id=_required_string(values, "run_id"),
        )


def _required_string(values: Mapping[str, object], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise KeyError(f"missing run context value: {key}")
    return value

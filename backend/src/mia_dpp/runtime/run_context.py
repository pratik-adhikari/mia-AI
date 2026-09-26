"""Architecture-neutral identity of one durable product-processing run."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RunContext:
    """Validated identity of one durable execution.

    All four identifiers are required regardless of whether the context comes
    from LangGraph, an agentic controller, a GUI pipeline, CLI, or batch job.
    """

    user_id: str
    thread_id: str
    product_id: str
    run_id: str

    def __post_init__(self) -> None:
        for name in ("user_id", "thread_id", "product_id", "run_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> RunContext:
        """Build a validated context without depending on an orchestrator state type."""

        return cls(
            user_id=_required_string(values, "user_id"),
            thread_id=_required_string(values, "thread_id"),
            product_id=_required_string(values, "product_id"),
            run_id=_required_string(values, "run_id"),
        )


def _required_string(values: Mapping[str, object], key: str) -> str:
    if key not in values:
        raise KeyError(f"missing run context value: {key}")
    value = values[key]
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a non-empty string")
    return value

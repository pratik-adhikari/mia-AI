"""Serializable pipeline definitions suitable for configuration or a future GUI builder."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mia_dpp.capabilities.catalog import component_catalog


class PipelineStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100)
    component: str = Field(min_length=1, max_length=120)
    after: tuple[str, ...] = ()
    config: dict[str, Any] = Field(default_factory=dict)


class PipelineDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    id: str = Field(min_length=1, max_length=100)
    privacy_mode: Literal["local", "shareable", "hybrid"] = "local"
    steps: tuple[PipelineStep, ...]

    @model_validator(mode="after")
    def validate_graph(self) -> "PipelineDefinition":
        known_components = component_catalog()
        step_ids = {step.id for step in self.steps}
        if len(step_ids) != len(self.steps):
            raise ValueError("pipeline step IDs must be unique")
        for step in self.steps:
            if step.component not in known_components:
                raise ValueError(f"unknown component: {step.component}")
            unknown_dependencies = set(step.after) - step_ids
            if unknown_dependencies:
                names = ", ".join(sorted(unknown_dependencies))
                raise ValueError(f"step {step.id!r} depends on unknown steps: {names}")
            if step.id in step.after:
                raise ValueError(f"step {step.id!r} cannot depend on itself")
        return self

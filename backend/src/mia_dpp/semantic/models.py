"""Context-preserving semantic-routing contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from mia_dpp.domain.base import WireModel


class ContextScope(StrEnum):
    PROPERTY = "property"
    PARENT = "parent"
    SIBLINGS = "siblings"
    SECTION = "section"
    FULL_PRODUCT = "full_product"


class ContextView(WireModel):
    """One non-destructive view of evidence at a specific semantic scope."""

    id: str = Field(min_length=1)
    focus_evidence_id: str = Field(min_length=1)
    scope: ContextScope
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    context_path: tuple[str, ...] = ()

    @model_validator(mode="after")
    def focus_is_included(self) -> ContextView:
        if self.focus_evidence_id not in self.evidence_ids:
            raise ValueError("context view must include its focus evidence")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("context view evidence IDs must be unique")
        return self


class ContextViewSet(WireModel):
    """All contextual views generated for one normalized evidence package."""

    normalization_version: str = Field(min_length=1)
    views: tuple[ContextView, ...]

    @model_validator(mode="after")
    def view_ids_are_unique(self) -> ContextViewSet:
        identifiers = [item.id for item in self.views]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("context view IDs must be unique")
        return self

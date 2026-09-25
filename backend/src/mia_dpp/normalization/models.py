"""Typed contracts for derived, lossless evidence normalization."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from mia_dpp.domain.base import WireModel


class NormalizedValueKind(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    QUANTITY = "quantity"
    RANGE = "range"
    TOLERANCE = "tolerance"
    BOOLEAN = "boolean"


class NormalizationStatus(StrEnum):
    NORMALIZED = "normalized"
    UNCHANGED = "unchanged"
    AMBIGUOUS = "ambiguous"


class NormalizationOrigin(StrEnum):
    DETERMINISTIC = "deterministic"
    JEV = "jev"
    UNCHANGED = "unchanged"


class NormalizedValue(WireModel):
    """One derived representation; raw source text remains authoritative."""

    raw: str
    kind: NormalizedValueKind
    number: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    boolean: bool | None = None
    unit: str | None = None
    qualifier: str | None = None

    @model_validator(mode="after")
    def shape_matches_kind(self) -> NormalizedValue:
        if (
            self.kind
            in {
                NormalizedValueKind.NUMBER,
                NormalizedValueKind.QUANTITY,
                NormalizedValueKind.TOLERANCE,
            }
            and self.number is None
        ):
            raise ValueError(f"{self.kind.value} normalized values require number")
        if self.kind is NormalizedValueKind.RANGE and (
            self.minimum is None or self.maximum is None
        ):
            raise ValueError("range normalized values require minimum and maximum")
        if self.kind is NormalizedValueKind.BOOLEAN and self.boolean is None:
            raise ValueError("boolean normalized values require boolean")
        return self


class NormalizedEvidence(WireModel):
    """Derived normalization for exactly one immutable EvidenceRecord."""

    evidence_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    context_path: tuple[str, ...] = ()
    value: NormalizedValue
    status: NormalizationStatus
    origin: NormalizationOrigin
    ambiguity_reason: str | None = None

    @model_validator(mode="after")
    def ambiguity_has_reason(self) -> NormalizedEvidence:
        if self.status is NormalizationStatus.AMBIGUOUS and not self.ambiguity_reason:
            raise ValueError("ambiguous normalization requires a reason")
        return self


class NormalizationReport(WireModel):
    """Complete normalization artifact for one evidence package."""

    version: str = Field(min_length=1)
    evidence: tuple[NormalizedEvidence, ...]

    @model_validator(mode="after")
    def evidence_ids_are_unique(self) -> NormalizationReport:
        identifiers = [item.evidence_id for item in self.evidence]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("normalized evidence IDs must be unique")
        return self

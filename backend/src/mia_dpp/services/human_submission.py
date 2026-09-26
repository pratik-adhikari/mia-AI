"""Neutral human-submission models consumed by reusable mapping capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ReviewDecision = Literal[
    "keep",
    "change_target",
    "unmapped",
    "irrelevant",
    "reject",
    "approve",
    "correct",
]


@dataclass(frozen=True, slots=True)
class MappingReviewDecision:
    review_id: str
    decision: ReviewDecision = "keep"
    corrected_requirement_id: str | None = None
    corrected_semantic_id: str | None = None
    corrected_value: str | None = None
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class MappingReviewSubmission:
    actor_name: str | None
    decisions: tuple[MappingReviewDecision, ...]


@dataclass(frozen=True, slots=True)
class HumanValueSubmission:
    value: str
    use_dummy: bool
    actor_name: str | None

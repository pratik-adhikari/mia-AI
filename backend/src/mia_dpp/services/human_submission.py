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

    def to_wire_dict(self) -> dict[str, str | None]:
        """Preserve the established persisted review-decision field names."""

        return {
            "reviewId": self.review_id,
            "decision": self.decision,
            "correctedRequirementId": self.corrected_requirement_id,
            "correctedSemanticId": self.corrected_semantic_id,
            "correctedValue": self.corrected_value,
            "comment": self.comment,
        }


@dataclass(frozen=True, slots=True)
class MappingReviewSubmission:
    actor_name: str | None
    decisions: tuple[MappingReviewDecision, ...]


@dataclass(frozen=True, slots=True)
class HumanValueSubmission:
    value: str
    use_dummy: bool
    actor_name: str | None

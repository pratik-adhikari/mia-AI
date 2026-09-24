"""Mapping, assessment, coverage, and semantic-review concepts."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, computed_field, model_validator

from mia_dpp.domain.base import WireModel
from mia_dpp.domain.evidence import EvidenceRecord
from mia_dpp.domain.targets import SemanticReference, TemplateIndex


class MappingStatus(StrEnum):
    AUTO = "auto"
    REVIEW = "review"
    APPROVED = "approved"
    REJECTED = "rejected"


class MappingOrigin(StrEnum):
    DETERMINISTIC = "deterministic"
    SEMANTIC_AGENT = "semantic_agent"
    HUMAN = "human"


class EvidenceOutcomeStatus(StrEnum):
    MAPPED = "mapped"
    UNCERTAIN = "uncertain"
    UNMAPPED = "unmapped"
    IRRELEVANT = "irrelevant"
    REJECTED = "rejected"


class EvidenceOutcome(WireModel):
    """Exactly one auditable semantic outcome for one evidence record."""

    evidence_id: str = Field(min_length=1)
    status: EvidenceOutcomeStatus
    requirement_id: str | None = None
    alternative_requirement_ids: tuple[str, ...] = ()
    reason: str = Field(min_length=1, max_length=600)
    mapping_origin: MappingOrigin

    @model_validator(mode="after")
    def target_matches_status(self) -> EvidenceOutcome:
        if self.status is EvidenceOutcomeStatus.MAPPED and self.requirement_id is None:
            raise ValueError("mapped evidence requires a requirement")
        if self.status in {
            EvidenceOutcomeStatus.UNMAPPED,
            EvidenceOutcomeStatus.IRRELEVANT,
            EvidenceOutcomeStatus.REJECTED,
        } and (self.requirement_id is not None or self.alternative_requirement_ids):
            raise ValueError("unmapped, irrelevant, or rejected evidence cannot have targets")
        return self


class LlmReview(WireModel):
    """Concise inspectable explanation for an AI-assisted mapping proposal."""

    conclusion: str = Field(min_length=1, max_length=500)
    rationale: str = Field(min_length=1, max_length=600)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    alternative_target_ids: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()


class CoverageStatus(StrEnum):
    SATISFIED = "satisfied"
    CANDIDATE = "candidate"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"


class MappingBasis(StrEnum):
    """Auditable reason a mapping exists; deliberately not a probability."""

    EXACT = "exact"
    SEMANTIC = "semantic"
    HUMAN = "human"


class MappingAssessment(WireModel):
    """Explain why a mapping is safe or why a person must review it."""

    basis: MappingBasis
    review_required: bool
    reason: str = Field(min_length=1)
    uncertainties: tuple[str, ...] = ()


class RequirementCoverage(WireModel):
    """Deterministic coverage state for exactly one official requirement."""

    requirement_id: str = Field(pattern=r"^req-[0-9a-f]{24}$")
    status: CoverageStatus
    supporting_evidence_ids: tuple[str, ...] = ()
    candidate_evidence_ids: tuple[str, ...] = ()
    match_method: str = Field(min_length=1)
    explanation: str = Field(min_length=1)

    @model_validator(mode="after")
    def evidence_matches_status(self) -> RequirementCoverage:
        supporting = set(self.supporting_evidence_ids)
        candidates = set(self.candidate_evidence_ids)
        if len(supporting) != len(self.supporting_evidence_ids):
            raise ValueError("supporting evidence IDs must be unique")
        if len(candidates) != len(self.candidate_evidence_ids):
            raise ValueError("candidate evidence IDs must be unique")
        if supporting & candidates:
            raise ValueError("evidence cannot be both supporting and candidate")
        if self.status is CoverageStatus.SATISFIED:
            if not supporting or candidates:
                raise ValueError("satisfied coverage requires only supporting evidence")
        elif self.status in {CoverageStatus.CANDIDATE, CoverageStatus.AMBIGUOUS}:
            if supporting or not candidates:
                raise ValueError("unresolved coverage requires only candidate evidence")
        elif supporting or candidates:
            raise ValueError("missing coverage cannot reference evidence")
        return self


class CoverageStatistics(WireModel):
    selected_templates: int = Field(ge=1)
    requirements: int = Field(ge=0)
    required_requirements: int = Field(ge=0)
    required_satisfied: int = Field(ge=0)
    required_candidate: int = Field(ge=0)
    required_ambiguous: int = Field(ge=0)
    required_missing: int = Field(ge=0)
    optional_requirements: int = Field(ge=0)
    optional_satisfied: int = Field(ge=0)
    optional_candidate: int = Field(ge=0)
    optional_ambiguous: int = Field(ge=0)
    optional_missing: int = Field(ge=0)
    evidence_records: int = Field(ge=0)
    evidence_used: int = Field(ge=0)
    unmatched_evidence: int = Field(ge=0)


class CoverageReport(WireModel):
    """Bidirectional accounting between retained evidence and target requirements."""

    inventory: TemplateIndex
    coverage: tuple[RequirementCoverage, ...]
    analyzed_evidence_ids: tuple[str, ...]
    unmatched_evidence_ids: tuple[str, ...]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def statistics(self) -> CoverageStatistics:
        """Derive summary counts from authoritative requirement coverage."""

        requirements = {item.id: item for item in self.inventory.requirements}

        def count(required: bool, status: CoverageStatus) -> int:
            return sum(
                requirements[item.requirement_id].required is required and item.status is status
                for item in self.coverage
            )

        required = sum(item.required for item in self.inventory.requirements)
        used = {
            evidence_id
            for item in self.coverage
            for evidence_id in (*item.supporting_evidence_ids, *item.candidate_evidence_ids)
        }
        return CoverageStatistics(
            selected_templates=len(self.inventory.selected_templates),
            requirements=len(self.coverage),
            required_requirements=required,
            required_satisfied=count(True, CoverageStatus.SATISFIED),
            required_candidate=count(True, CoverageStatus.CANDIDATE),
            required_ambiguous=count(True, CoverageStatus.AMBIGUOUS),
            required_missing=count(True, CoverageStatus.MISSING),
            optional_requirements=len(self.coverage) - required,
            optional_satisfied=count(False, CoverageStatus.SATISFIED),
            optional_candidate=count(False, CoverageStatus.CANDIDATE),
            optional_ambiguous=count(False, CoverageStatus.AMBIGUOUS),
            optional_missing=count(False, CoverageStatus.MISSING),
            evidence_records=len(self.analyzed_evidence_ids),
            evidence_used=len(used),
            unmatched_evidence=len(self.unmatched_evidence_ids),
        )

    @model_validator(mode="after")
    def requirements_and_evidence_are_accounted_for(self) -> CoverageReport:
        expected_requirements = [item.id for item in self.inventory.requirements]
        actual_requirements = [item.requirement_id for item in self.coverage]
        if actual_requirements != expected_requirements:
            raise ValueError("coverage must follow and include every inventory requirement")
        if len(self.analyzed_evidence_ids) != len(set(self.analyzed_evidence_ids)):
            raise ValueError("analyzed evidence IDs must be unique")
        if len(self.unmatched_evidence_ids) != len(set(self.unmatched_evidence_ids)):
            raise ValueError("unmatched evidence IDs must be unique")
        used = {
            evidence_id
            for item in self.coverage
            for evidence_id in (*item.supporting_evidence_ids, *item.candidate_evidence_ids)
        }
        analyzed = set(self.analyzed_evidence_ids)
        unmatched = set(self.unmatched_evidence_ids)
        if used & unmatched or used | unmatched != analyzed:
            raise ValueError("every analyzed evidence record must be used or unmatched")
        return self


class MappingTarget(WireModel):
    template_key: str
    template_release: str
    template_path: tuple[str, ...] = Field(min_length=1)
    instance_path: tuple[str, ...] = Field(min_length=1)
    id_short: str = Field(min_length=1)
    semantic_id: SemanticReference


class FieldMapping(WireModel):
    """One auditable evidence-to-target mapping at any decision status."""

    id: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    source_field: str
    source_value: str
    target: MappingTarget
    assessment: MappingAssessment
    reasoning: str
    status: MappingStatus
    mapping_origin: MappingOrigin = MappingOrigin.DETERMINISTIC
    human_reviewed: bool = False
    human_actor_name: str | None = Field(default=None, max_length=200)
    human_value_kind: Literal["verified", "dummy"] | None = None
    llm_review: LlmReview | None = None
    human_comment: str | None = Field(default=None, max_length=1000)


class MappingResult(WireModel):
    """Downstream mapping outcome; unmatched evidence remains in the knowledge package."""

    mapped: tuple[FieldMapping, ...] = ()
    ambiguous: tuple[FieldMapping, ...] = ()
    rejected: tuple[FieldMapping, ...] = ()
    unmatched_evidence_ids: tuple[str, ...] = ()
    irrelevant_evidence_ids: tuple[str, ...] = ()
    rejected_evidence_ids: tuple[str, ...] = ()
    outcomes: tuple[EvidenceOutcome, ...] = ()

    @model_validator(mode="after")
    def evidence_outcomes_are_unique(self) -> MappingResult:
        proposals = [item.evidence_id for item in (*self.mapped, *self.ambiguous, *self.rejected)]
        if len(proposals) != len(set(proposals)):
            raise ValueError("an evidence record must have exactly one mapping outcome")
        unmatched = set(self.unmatched_evidence_ids)
        if len(unmatched) != len(self.unmatched_evidence_ids):
            raise ValueError("unmatched evidence IDs must be unique")
        irrelevant = set(self.irrelevant_evidence_ids)
        rejected_ids = set(self.rejected_evidence_ids)
        if len(irrelevant) != len(self.irrelevant_evidence_ids):
            raise ValueError("irrelevant evidence IDs must be unique")
        if len(rejected_ids) != len(self.rejected_evidence_ids):
            raise ValueError("rejected evidence IDs must be unique")
        accepted = {item.evidence_id for item in (*self.mapped, *self.ambiguous)}
        categories = (accepted, unmatched, irrelevant, rejected_ids)
        overlaps = (
            left & right
            for index, left in enumerate(categories)
            for right in categories[index + 1 :]
        )
        if any(overlaps):
            raise ValueError("evidence outcome categories must be disjoint")
        outcome_ids = [item.evidence_id for item in self.outcomes]
        if len(outcome_ids) != len(set(outcome_ids)):
            raise ValueError("evidence outcomes must be unique")
        return self


class TextMappingProposal(WireModel):
    product_name: str
    evidence: tuple[EvidenceRecord, ...]
    mappings: tuple[FieldMapping, ...]


class SemanticReviewItem(WireModel):
    """One row in the complete consolidated mapping review."""

    id: str = Field(pattern=r"^review-[0-9a-f]{24}$")
    evidence_id: str = Field(min_length=1)
    status: EvidenceOutcomeStatus
    requirement_id: str | None = Field(default=None, pattern=r"^req-[0-9a-f]{24}$")
    alternative_requirement_ids: tuple[str, ...] = ()
    reason: str = Field(min_length=1, max_length=600)
    mapping: FieldMapping | None = None

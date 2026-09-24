"""Application contracts for product website resolution."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import Field, model_validator

from mia_dpp.domain.base import WireModel
from mia_dpp.domain.evidence import EvidenceRecord, ProductKnowledgePackage
from mia_dpp.domain.mappings import MappingResult
from mia_dpp.domain.targets import Requirement, TemplateIndex


class SemanticMappingContext(WireModel):
    """Complete lean semantic problem safe to provide to semantic reasoning."""

    product_id: str
    evidence: tuple[EvidenceRecord, ...]
    requirements: tuple[Requirement, ...]


class LeanEvidence(WireModel):
    id: str
    label: str
    value: str
    unit: str | None = None
    context: tuple[str, ...] = ()


class LeanTarget(WireModel):
    id: str = Field(pattern=r"^req-[0-9a-f]{24}$")
    template: str
    name: str
    path: tuple[str, ...]
    description: str | None = None
    semantic_id: str | None = None
    supplemental_semantic_ids: tuple[str, ...] = ()
    model_type: str
    value_type: str | None = None
    unit: str | None = None
    allowed_values: tuple[str, ...] = ()
    required: bool
    conditional: bool
    wildcard: bool


class DeterministicMappingHint(WireModel):
    evidence_id: str
    requirement_id: str = Field(pattern=r"^req-[0-9a-f]{24}$")
    authoritative: bool = True


class EvidenceMappingDecision(WireModel):
    evidence_id: str
    status: Literal["mapped", "uncertain", "unmapped", "irrelevant"]
    requirement_id: str | None = None
    alternative_requirement_ids: tuple[str, ...] = ()
    reason: str = Field(min_length=1, max_length=600)

    @model_validator(mode="after")
    def status_has_valid_target_shape(self) -> EvidenceMappingDecision:
        if self.status == "mapped" and self.requirement_id is None:
            raise ValueError("mapped status requires a requirement")
        if self.status in {"unmapped", "irrelevant"} and (
            self.requirement_id is not None or self.alternative_requirement_ids
        ):
            raise ValueError("unmapped or irrelevant status cannot include targets")
        return self


class BatchSemanticMappingResult(WireModel):
    decisions: tuple[EvidenceMappingDecision, ...]


class SemanticMappingMetrics(WireModel):
    evidence_count: int = Field(ge=0)
    target_count: int = Field(ge=0)
    lean_evidence_json_bytes: int = Field(ge=0)
    lean_target_json_bytes: int = Field(ge=0)
    model_requests: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class SemanticMappingRun(WireModel):
    evidence: tuple[LeanEvidence, ...]
    targets: tuple[LeanTarget, ...]
    deterministic_mappings: tuple[DeterministicMappingHint, ...]
    result: BatchSemanticMappingResult
    metrics: SemanticMappingMetrics


class SemanticMapper(Protocol):
    async def map(
        self,
        package: ProductKnowledgePackage,
        template_index: TemplateIndex,
        deterministic: MappingResult,
        *,
        reviewed_knowledge: tuple[dict[str, object], ...] = (),
    ) -> SemanticMappingRun: ...


class MappingKnowledgeStatus(StrEnum):
    CANDIDATE = "candidate"
    TRUSTED = "trusted"


class MappingKnowledgeScope(StrEnum):
    USER = "user"
    ORGANIZATION = "organization"
    GLOBAL = "global"


class MappingKnowledgeEntry(WireModel):
    """Reviewed mapping knowledge reusable as non-authoritative semantic context."""

    id: str
    scope: MappingKnowledgeScope = MappingKnowledgeScope.USER
    owner_id: str | None = "local-development"
    source_field: str
    example_values: tuple[str, ...]
    target_template: str
    target_path: tuple[str, ...]
    semantic_id: str
    manufacturer: str | None = None
    domain: str | None = None
    product_family: str | None = None
    llm_review_summary: str | None = None
    human_comments: tuple[str, ...] = ()
    confirmations: int = 0
    corrections: int = 0
    rejections: int = 0
    created_at: datetime
    updated_at: datetime
    status: MappingKnowledgeStatus

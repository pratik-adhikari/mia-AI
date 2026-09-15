"""Source facts, provenance, and product knowledge."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from mia_dpp.domain.base import WireModel, utc_now


class EvidenceStatus(StrEnum):
    OBSERVED = "observed"
    INFERRED = "inferred"
    VERIFIED = "verified"
    CONFLICTING = "conflicting"
    REJECTED = "rejected"


class SourceType(StrEnum):
    WEBSITE = "website"
    HUMAN = "human"


class SourceLocation(WireModel):
    """Location of one fact in its original source."""

    page: int | None = Field(default=None, ge=1)
    selector: str | None = None
    base_selector: str | None = None
    schema_id: str | None = None
    record_path: tuple[int, ...] = ()
    json_pointer: str | None = None
    excerpt: str | None = None
    table: str | None = None
    cell: str | None = None


class EvidenceRecord(WireModel):
    """One traceable fact; the compiler never accepts a bare value."""

    id: str = Field(min_length=1)
    predicate: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    source_label: str | None = None
    canonical_predicate: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    value: JsonValue
    unit: str | None = None

    # Semantic source context retained from hierarchical source structures.
    # Example:
    # ("Submersible probe",)
    # + "Degree of protection"
    # = "IP68"
    context_path: tuple[str, ...] = ()

    source_type: SourceType = SourceType.WEBSITE
    source_uri: str = Field(min_length=1)
    source_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_location: SourceLocation
    extraction_method: str = Field(min_length=1)
    extractor_name: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    status: EvidenceStatus
    acquired_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def accepted_evidence_has_a_value(self) -> EvidenceRecord:
        if (
            self.status
            in {
                EvidenceStatus.OBSERVED,
                EvidenceStatus.INFERRED,
                EvidenceStatus.VERIFIED,
            }
            and self.value is None
        ):
            raise ValueError("usable evidence must contain a value")
        return self


class ProductKnowledgePackage(WireModel):
    """Framework-neutral evidence collected for one product."""

    product_id: str = Field(min_length=1)
    product_name: str = Field(min_length=1)
    source_artifact_ids: tuple[str, ...] = ()
    evidence: tuple[EvidenceRecord, ...]

    @model_validator(mode="after")
    def evidence_ids_are_unique(self) -> ProductKnowledgePackage:
        identifiers = [item.id for item in self.evidence]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("evidence IDs must be unique")
        return self


class DocumentReference(WireModel):
    """Content-addressed local document discovered from product evidence."""

    uri: str = Field(min_length=1)
    local_path: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str = Field(min_length=1)
    acquired_at: AwareDatetime = Field(default_factory=utc_now)

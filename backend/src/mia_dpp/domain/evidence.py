"""Source facts, provenance, and product knowledge."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

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
    acquired_sources: tuple[AcquiredSource, ...] = ()
    # Optional exploration failures remain auditable without discarding valid seed evidence.
    source_failures: tuple[SourceAcquisitionFailure, ...] = ()
    # Retain the readable hierarchy separately from flattened EvidenceRecords for audits/UI use.
    extracted_pages: tuple[ExtractedProductPage, ...] = ()
    evidence: tuple[EvidenceRecord, ...]

    @model_validator(mode="after")
    def evidence_ids_are_unique(self) -> ProductKnowledgePackage:
        identifiers = [item.id for item in self.evidence]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("evidence IDs must be unique")
        return self


class AcquiredSource(WireModel):
    """Exact framework-neutral source retained for audit and artifact storage."""

    id: str = Field(pattern=r"^source-[a-z]+-[0-9a-f]{24}$")
    final_url: str = Field(min_length=1)
    rendered_html: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    acquired_at: AwareDatetime


class SourceAcquisitionFailure(WireModel):
    """One non-fatal failure while acquiring an LLM-selected secondary source."""

    url: str = Field(min_length=1)
    error: str = Field(min_length=1, max_length=2000)


class ExtractedProperty(WireModel):
    """One human-readable label/value pair before evidence normalization."""

    label: str = Field(
        min_length=1,
        max_length=200,
        description="The visible fact or bullet label, without its parent heading.",
    )
    value: str = Field(
        min_length=1,
        max_length=4000,
        description="The visible value or explanation, without repeating the label.",
    )
    unit: str | None = Field(default=None, max_length=100)
    source_excerpt: str | None = Field(default=None, max_length=1000)


class ExtractedSection(WireModel):
    """Facts grouped by their visible outer-to-inner page hierarchy."""

    context_path: tuple[str, ...] = Field(
        description=(
            "Complete outer-to-inner visible hierarchy. Create a separate section for every "
            "heading/subheading path, including empty product tabs."
        )
    )
    properties: tuple[ExtractedProperty, ...] = ()


class ExtractedAsset(WireModel):
    """A source-observed image or document and its local audit reference."""

    url: str = Field(
        min_length=1,
        description="Exact image or document URL copied verbatim from the rendered page.",
    )
    kind: Literal["image", "document"]
    label: str = Field(min_length=1, max_length=300)
    context_path: tuple[str, ...] = ()
    # The workflow assigns image roles after downloads reveal which candidate is usable/largest.
    role: Literal["main", "supporting"] | None = None
    # This remains unset during extraction and is filled only after durable artifact storage.
    workspace_path: str | None = None


class ExtractedProductPage(WireModel):
    """Strict, source-neutral representation shared by Crawl4AI and the HTML fallback."""

    source_url: str = Field(min_length=1)
    product_name: str = Field(min_length=1, max_length=300)
    product_type: str | None = Field(
        default=None,
        max_length=300,
        description="Visible product category/type directly beneath or beside the product name.",
    )
    summary: str | None = Field(
        default=None,
        max_length=2000,
        description="Visible introductory product description copied faithfully.",
    )
    sections: tuple[ExtractedSection, ...]
    assets: tuple[ExtractedAsset, ...] = ()


class DocumentReference(WireModel):
    """Content-addressed local document discovered from product evidence."""

    uri: str = Field(min_length=1)
    local_path: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str = Field(min_length=1)
    acquired_at: AwareDatetime = Field(default_factory=utc_now)

"""Durable artifact metadata shared by storage, API, and workspace views."""

from enum import StrEnum

from pydantic import AwareDatetime, Field

from mia_dpp.domain.base import WireModel, utc_now


class ArtifactKind(StrEnum):
    SEARCH = "search"
    SOURCE = "source"
    RAW = "raw"
    EVIDENCE = "evidence"
    MAPPING = "mapping"
    COVERAGE = "coverage"
    REVIEW = "review"
    AAS = "aas"
    VALIDATION = "validation"
    EXPORT = "export"


class StoredArtifact(WireModel):
    """Deployment-neutral identity for bytes stored on disk or object storage."""

    id: str
    key: str
    content_type: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    storage_uri: str
    product_id: str | None = None
    run_id: str | None = None
    derived_from: tuple[str, ...] = ()
    created_at: AwareDatetime = Field(default_factory=utc_now)


class WorkspaceArtifact(WireModel):
    """Stable API view of one stored workflow artifact."""

    id: str
    kind: ArtifactKind
    name: str
    relative_path: str
    created_at: AwareDatetime
    created_by: str
    content_type: str
    sha256: str
    size: int
    product_id: str | None = None
    source_url: str | None = None
    derived_from: tuple[str, ...] = ()
    downloadable: bool = True

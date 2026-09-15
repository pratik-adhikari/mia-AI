"""Small MIA-facing contracts around Crawl4AI website results."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any, Protocol

from pydantic import Field

from mia_dpp.domain.base import WireModel
from mia_dpp.errors import ExtractionError

ExtractionSchema = dict[str, Any]


class ExtractionDependencyError(ExtractionError):
    """A selected web implementation is not configured or installed."""


class ProductUrlRejectedError(ExtractionError):
    """A URL violates the configured admission policy."""


class PageLoadError(ExtractionError):
    """The page loader could not return usable website data."""


class SourceAssetKind(StrEnum):
    IMAGE = "image"
    DOCUMENT = "document"


class SourceAsset(WireModel):
    """A relevant image or document link discovered on a product page."""

    id: str = Field(min_length=1)
    kind: SourceAssetKind
    url: str = Field(min_length=1)
    label: str | None = None
    media_type: str | None = None
    score: float | None = None
    width: int | None = None
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    size: int | None = Field(default=None, ge=0)


@dataclass(frozen=True)
class SourceLink:
    url: str
    text: str = ""
    title: str = ""
    media_type: str | None = None


@dataclass(frozen=True)
class SourceImage:
    url: str
    alt: str = ""
    title: str = ""
    score: float | None = None
    width: int | None = None


@dataclass(frozen=True)
class RenderedPage:
    """The useful subset of one Crawl4AI result retained by MIA."""

    url: str
    html: str
    acquired_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    structured_data: tuple[dict[str, Any], ...] = ()
    links: tuple[SourceLink, ...] = ()
    images: tuple[SourceImage, ...] = ()
    mhtml: str | None = None
    structure_fingerprint: str | None = None
    schema_id: str | None = None
    base_selector: str | None = None
    assets: tuple[SourceAsset, ...] = ()

    def __post_init__(self) -> None:
        if not self.url:
            raise ValueError("rendered page URL must not be empty")
        if self.acquired_at.tzinfo is None or self.acquired_at.utcoffset() is None:
            raise ValueError("rendered page acquisition time must include a timezone")

    @property
    def content_sha256(self) -> str:
        return sha256(self.html.encode("utf-8")).hexdigest()


class PageLoader(Protocol):
    async def load(self, url: str, schema: ExtractionSchema | None = None) -> RenderedPage: ...

    def extract(
        self, page: RenderedPage, schema: ExtractionSchema
    ) -> tuple[dict[str, Any], ...]: ...

    async def generate_schema(self, page: RenderedPage) -> ExtractionSchema: ...

    async def discover(self, url: str) -> tuple[SourceLink, ...]: ...

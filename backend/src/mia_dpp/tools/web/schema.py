"""Persist validated Crawl4AI extraction schemas as small JSON records."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import AwareDatetime, Field

from mia_dpp.domain.base import WireModel, utc_now


class WebExtractionSchema(WireModel):
    """A tested deterministic schema reusable for one website layout."""

    id: str = Field(min_length=1)
    site: str = Field(min_length=1)
    extraction_schema: dict[str, Any] = Field(alias="schema")
    example_url: str = Field(min_length=1)
    created_at: AwareDatetime = Field(default_factory=utc_now)
    structure_fingerprint: str | None = None
    version: int = Field(default=1, ge=1)
    status: str = "trusted"


class WebSchemaStore:
    """Load bundled schemas and save runtime-generated schemas as configuration."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root
        self._bundled = Path(__file__).with_name("schemas")

    def load(self, url: str, fingerprint: str | None = None) -> WebExtractionSchema | None:
        site = _site(url)
        names = ([f"{site}-{_fingerprint(fingerprint)}.json"] if fingerprint else []) + [
            f"{site}.json"
        ]
        paths = [root / name for root in (self._root, self._bundled) if root for name in names]
        for path in paths:
            if path.exists():
                record = WebExtractionSchema.model_validate_json(path.read_text(encoding="utf-8"))
                return record if record.status == "trusted" else None
        return None

    def save_trusted(
        self,
        url: str,
        schema: dict[str, Any],
        *,
        structure_fingerprint: str | None,
    ) -> WebExtractionSchema:
        if self._root is None:
            raise ValueError("a writable web schema directory is not configured")
        site = _site(url)
        digest = hashlib.sha256(f"{site}\0{schema!r}".encode()).hexdigest()[:20]
        record = WebExtractionSchema(
            id=f"web-schema-{digest}",
            site=site,
            extraction_schema=schema,
            example_url=url,
            structure_fingerprint=structure_fingerprint,
        )
        self._root.mkdir(parents=True, exist_ok=True)
        suffix = f"-{_fingerprint(structure_fingerprint)}" if structure_fingerprint else ""
        target = self._root / f"{site}{suffix}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(target)
        return record


def _site(url: str) -> str:
    host = (urlsplit(url).hostname or "").casefold().removeprefix("www.")
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789.-"
    if not host or any(character not in allowed for character in host):
        raise ValueError("schema URL must contain a valid hostname")
    return host


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]

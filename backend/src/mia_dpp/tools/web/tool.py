"""MIA's thin policy and evidence boundary over Crawl4AI website results."""

from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import replace
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import httpx

from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.errors import ExtractionError
from mia_dpp.tools.web.models import (
    PageLoader,
    RenderedPage,
    SourceAsset,
    SourceAssetKind,
    SourceLink,
)
from mia_dpp.tools.web.normalize import normalize
from mia_dpp.tools.web.schema import WebExtractionSchema, WebSchemaStore
from mia_dpp.tools.web.url_policy import ProductUrlPolicy

_DOCUMENT_TYPES = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".csv",
    ".zip",
    ".step",
    ".stp",
    ".dxf",
}
_IMAGE_NOISE = ("logo", "icon", "tracking", "pixel", "social", "avatar")
_DOCUMENT_HINTS = ("datasheet", "data sheet", "manual", "certificate", "drawing", "cad")
_MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024


class WebExtractionTool:
    """Acquire structured website evidence while enforcing MIA trust policy."""

    def __init__(
        self,
        *,
        loader: PageLoader,
        url_policy: ProductUrlPolicy | None = None,
        schemas: WebSchemaStore | None = None,
    ) -> None:
        self._loader = loader
        self._url_policy = url_policy or ProductUrlPolicy()
        self._schemas = schemas or WebSchemaStore()

    async def extract(self, url: str) -> tuple[ProductKnowledgePackage, RenderedPage]:
        """Render a URL, apply or learn a tested schema, then create evidence."""

        requested_url = await self._url_policy.validate(url)
        initial_schema = self._schemas.load(requested_url)
        page = await self._loader.load(
            requested_url,
            initial_schema.extraction_schema if initial_schema else None,
        )
        final_url = await self._url_policy.validate(page.url)
        page = replace(page, url=final_url, assets=_assets(page))
        schema = self._schemas.load(final_url, page.structure_fingerprint) or initial_schema
        if schema is not None:
            page = self._with_schema(page, schema, reuse_result=schema is initial_schema)
            package = self._package(page, schema)
            if package is not None:
                return package, page
        elif page.structured_data:
            package = self._package(page, None)
            if package is not None:
                return package, page

        candidate = await self._loader.generate_schema(page)
        data = self._loader.extract(page, candidate)
        candidate_page = replace(
            page,
            structured_data=data,
            base_selector=_base_selector(candidate),
        )
        package = self._package(candidate_page, None)
        if package is None:
            raise ExtractionError("Crawl4AI schema returned no meaningful product facts")
        trusted = self._schemas.save_trusted(
            final_url,
            candidate,
            structure_fingerprint=page.structure_fingerprint,
        )
        page = replace(candidate_page, schema_id=trusted.id)
        package = self._package(page, trusted)
        if package is None:  # pragma: no cover - same data was validated above
            raise ExtractionError("validated schema could not be normalized")
        return package, page

    async def download_source_asset(
        self, url: str, *, max_bytes: int = _MAX_DOWNLOAD_BYTES
    ) -> tuple[SourceAsset, bytes]:
        """Download one explicitly selected asset with redirect and size checks."""

        current = await self._url_policy.validate(url)
        async with httpx.AsyncClient(follow_redirects=False, timeout=30) as client:
            for _ in range(6):
                async with client.stream("GET", current) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise ExtractionError("asset redirect did not include a location")
                        current = await self._url_policy.validate(str(response.url.join(location)))
                        continue
                    response.raise_for_status()
                    declared = int(response.headers.get("content-length", "0") or 0)
                    if declared > max_bytes:
                        raise ExtractionError("source asset exceeds the download size limit")
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise ExtractionError("source asset exceeds the download size limit")
                        chunks.append(chunk)
                    data = b"".join(chunks)
                    media_type = response.headers.get("content-type", "").split(";", 1)[0] or None
                    if not data or media_type in {"text/html", "application/xhtml+xml"}:
                        raise ExtractionError("source asset response is empty or is an HTML page")
                    return _downloaded_asset(current, data, media_type), data
        raise ExtractionError("source asset redirected too many times")

    async def discover_related_sources(self, url: str) -> tuple[SourceLink, ...]:
        """Discover bounded same-site source pages through Crawl4AI."""

        requested = await self._url_policy.validate(url)
        links = await self._loader.discover(requested)
        accepted = []
        for link in links:
            try:
                accepted.append(replace(link, url=await self._url_policy.validate(link.url)))
            except ExtractionError:
                continue
        return tuple(accepted)

    def _with_schema(
        self, page: RenderedPage, schema: WebExtractionSchema, *, reuse_result: bool
    ) -> RenderedPage:
        data = (
            page.structured_data
            if reuse_result and page.structured_data
            else self._loader.extract(page, schema.extraction_schema)
        )
        return replace(
            page,
            structured_data=data,
            schema_id=schema.id,
            base_selector=_base_selector(schema.extraction_schema),
        )

    @staticmethod
    def _package(
        page: RenderedPage, schema: WebExtractionSchema | None
    ) -> ProductKnowledgePackage | None:
        evidence, product_name = normalize(page, schema.extraction_schema if schema else {})
        meaningful = [
            item
            for item in evidence
            if item.source_label not in {"Product name", "Product page URL"}
        ]
        if not meaningful:
            return None
        source_id = f"source-web-{page.content_sha256[:24]}"
        product_id = hashlib.sha256(f"{page.url}\0{product_name}".encode()).hexdigest()[:24]
        return ProductKnowledgePackage(
            product_id=f"product-{product_id}",
            product_name=product_name,
            source_artifact_ids=(source_id,),
            evidence=evidence,
        )


def _assets(page: RenderedPage) -> tuple[SourceAsset, ...]:
    assets: dict[str, SourceAsset] = {}
    for link in page.links:
        suffix = PurePosixPath(urlsplit(link.url).path).suffix.casefold()
        label = link.text or link.title
        is_document = (
            suffix in _DOCUMENT_TYPES
            or bool(link.media_type and link.media_type != "text/html")
            or any(hint in label.casefold() for hint in _DOCUMENT_HINTS)
        )
        if is_document:
            assets[link.url] = _asset(
                SourceAssetKind.DOCUMENT,
                link.url,
                label,
                media_type=link.media_type or mimetypes.guess_type(link.url)[0],
            )
    for image in page.images:
        identity = f"{image.url} {image.alt} {image.title}".casefold()
        if (image.score is not None and image.score < 2) or (image.width and image.width < 80):
            continue
        if any(word in identity for word in _IMAGE_NOISE):
            continue
        assets[image.url] = _asset(
            SourceAssetKind.IMAGE,
            image.url,
            image.alt or image.title,
            score=image.score,
            width=image.width,
        )
    return tuple(assets.values())


def _asset(kind: SourceAssetKind, url: str, label: str, **details: object) -> SourceAsset:
    digest = hashlib.sha256(f"{kind}\0{url}".encode()).hexdigest()[:24]
    return SourceAsset(id=f"asset-{digest}", kind=kind, url=url, label=label or None, **details)


def _downloaded_asset(url: str, data: bytes, media_type: str | None) -> SourceAsset:
    kind = (
        SourceAssetKind.IMAGE
        if (media_type or "").startswith("image/")
        else SourceAssetKind.DOCUMENT
    )
    asset = _asset(kind, url, PurePosixPath(urlsplit(url).path).name, media_type=media_type)
    return asset.model_copy(
        update={"content_sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    )


def _base_selector(schema: dict[str, object]) -> str | None:
    value = schema.get("baseSelector")
    return value if isinstance(value, str) else None

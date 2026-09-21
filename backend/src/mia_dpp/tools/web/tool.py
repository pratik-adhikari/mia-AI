"""The agent-facing capability that turns one URL into source evidence."""

from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import replace
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import httpx

from mia_dpp.domain.evidence import (
    AcquiredSource,
    ProductKnowledgePackage,
)
from mia_dpp.errors import ExtractionError
from mia_dpp.tools.web.generic import WebsiteFactExtractor
from mia_dpp.tools.web.models import (
    DownloadedSource,
    PageLoader,
    RenderedPage,
    SourceExplorationPlanner,
    SourceLink,
)
from mia_dpp.tools.web.url_policy import ProductUrlPolicy

_MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
# Images use the same explicit, SSRF-checked downloader as technical documents; extraction does
# not implicitly fetch arbitrary page resources.
_DOWNLOAD_SUFFIXES = frozenset(
    {
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".zip", ".step", ".stp",
        ".dxf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg",
    }
)
_DOWNLOAD_MEDIA_TYPES = frozenset(
    {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
        "application/octet-stream",
        "model/step",
        "text/csv",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/svg+xml",
        "image/webp",
    }
)


class WebExtractionTool:
    """Turn one public URL into provenance-rich product evidence.

    MIA agent or the direct website workflow calls this capability. It validates
    and loads the page, extracts facts, and normalizes evidence, but never maps
    facts to AAS targets.
    """

    def __init__(
        self,
        *,
        loader: PageLoader,
        url_policy: ProductUrlPolicy | None = None,
        fact_extractor: WebsiteFactExtractor | None = None,
        source_planner: SourceExplorationPlanner | None = None,
    ) -> None:
        self._loader = loader
        self._url_policy = url_policy or ProductUrlPolicy()
        self._fact_extractor = fact_extractor or WebsiteFactExtractor()
        self._source_planner = source_planner

    async def extract(self, url: str) -> ProductKnowledgePackage:
        """Return the seed product page immediately; deep research is a separate durable job."""

        requested_url = await self._url_policy.validate(url)
        seed_page = await self._validated_page(await self._loader.load(requested_url))
        return self._package(seed_page)

    async def extract_source(self, url: str) -> ProductKnowledgePackage:
        """Acquire one selected secondary source without another semantic extraction request."""

        requested_url = await self._url_policy.validate(url)
        secondary_loader = getattr(self._loader, "load_source", self._loader.load)
        page = await self._validated_page(await secondary_loader(requested_url))
        return self._package(page)

    async def select_deep_sources(
        self,
        *,
        seed_url: str,
        product_name: str,
    ) -> tuple[SourceLink, ...]:
        """Discover through Crawl4AI, then optionally apply the existing bounded LLM selector."""

        candidates = await self.discover_sources(seed_url)
        if self._source_planner is None:
            return candidates
        return await self._source_planner.select(
            seed_url=seed_url,
            product_name=product_name,
            candidates=candidates,
        )

    def _package(self, page: RenderedPage) -> ProductKnowledgePackage:
        evidence, product_name, extracted_page = self._fact_extractor.extract_structured(page)
        if not evidence:
            raise ExtractionError("the product page contained no useful structured facts")
        source_id = f"source-web-{page.content_sha256[:24]}"
        product_id = "product-" + hashlib.sha256(
            f"{page.url}\0{product_name}".encode()
        ).hexdigest()[:24]
        return ProductKnowledgePackage(
            product_id=product_id,
            product_name=product_name,
            source_artifact_ids=(source_id,),
            acquired_sources=(
                AcquiredSource(
                    id=source_id,
                    final_url=page.url,
                    rendered_html=page.html,
                    markdown=page.markdown,
                    structured_content=page.extracted_content,
                    content_sha256=page.content_sha256,
                    acquired_at=page.acquired_at,
                ),
            ),
            extracted_pages=(extracted_page,) if extracted_page is not None else (),
            evidence=evidence,
        )

    async def _validated_page(self, page: RenderedPage) -> RenderedPage:
        final_url = await self._url_policy.validate(page.url)
        return replace(page, url=final_url) if final_url != page.url else page

    async def discover_sources(self, url: str) -> tuple[SourceLink, ...]:
        """Discover a bounded set of related pages through the configured page loader."""

        requested = await self._url_policy.validate(url)
        discovered = await self._loader.discover(requested)
        accepted: list[SourceLink] = []
        for link in discovered:
            try:
                accepted.append(replace(link, url=await self._url_policy.validate(link.url)))
            except ExtractionError:
                continue
        return tuple(dict.fromkeys(accepted))

    async def download_source(
        self,
        url: str,
        *,
        max_bytes: int = _MAX_DOWNLOAD_BYTES,
    ) -> DownloadedSource:
        """Download one explicitly selected technical file with redirect and size checks."""

        current = await self._url_policy.validate(url)
        async with httpx.AsyncClient(follow_redirects=False, timeout=30) as client:
            for _ in range(6):
                async with client.stream("GET", current) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise ExtractionError("source redirect did not include a location")
                        current = await self._url_policy.validate(str(response.url.join(location)))
                        continue
                    response.raise_for_status()
                    declared = int(response.headers.get("content-length", "0") or 0)
                    if declared > max_bytes:
                        raise ExtractionError("source file exceeds the download size limit")
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise ExtractionError("source file exceeds the download size limit")
                        chunks.append(chunk)
                    data = b"".join(chunks)
                    if not data:
                        raise ExtractionError("source file is empty")
                    declared_type = response.headers.get("content-type", "").split(";", 1)[0]
                    guessed_type = mimetypes.guess_type(current)[0]
                    media_type = declared_type or guessed_type or "application/octet-stream"
                    suffix = PurePosixPath(urlsplit(current).path).suffix.casefold()
                    if media_type in {"text/html", "application/xhtml+xml"}:
                        raise ExtractionError("source file response is an HTML page")
                    if suffix not in _DOWNLOAD_SUFFIXES and media_type not in _DOWNLOAD_MEDIA_TYPES:
                        raise ExtractionError("source file type is not supported")
                    return DownloadedSource(
                        final_url=current,
                        media_type=media_type,
                        content_sha256=hashlib.sha256(data).hexdigest(),
                        size=len(data),
                        content=data,
                    )
        raise ExtractionError("source file redirected too many times")

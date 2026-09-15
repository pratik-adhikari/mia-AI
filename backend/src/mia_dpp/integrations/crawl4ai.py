"""Crawl4AI acquisition, structured extraction, and schema generation."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urljoin, urlsplit

from mia_dpp.tools.web.models import (
    ExtractionDependencyError,
    ExtractionSchema,
    PageLoadError,
    RenderedPage,
    SourceImage,
    SourceLink,
)

_SCHEMA_QUERY = """Extract product identity and technical source facts.
Return product.name, optional jsonLd, and sections. Each section has an optional
name, optional label/value/unit, and properties containing label/value/unit.
Preserve component/group names so repeated labels remain distinguishable."""
_TARGET_JSON = {
    "product": {"name": "Example controller"},
    "sections": [
        {
            "name": "Housing",
            "properties": [{"label": "Degree of protection", "value": "IP54", "unit": None}],
        }
    ],
}


class Crawl4AIPageLoader:
    """Expose only the Crawl4AI features consumed by MIA's web capability."""

    def __init__(self, *, schema_model: str | None = None, api_key: str | None = None) -> None:
        self._schema_model = schema_model
        self._api_key = api_key

    async def load(self, url: str, schema: ExtractionSchema | None = None) -> RenderedPage:
        """Render a page and retain structured JSON, links, media, and MHTML."""

        try:
            from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, JsonCssExtractionStrategy
        except ImportError as exc:  # pragma: no cover - required production dependency
            raise ExtractionDependencyError("Crawl4AI is not installed") from exc

        config = CrawlerRunConfig(
            extraction_strategy=JsonCssExtractionStrategy(schema) if schema else None,
            capture_mhtml=True,
            score_links=True,
            exclude_social_media_links=True,
            image_score_threshold=2,
        )
        try:
            async with AsyncWebCrawler() as crawler:
                result = await crawler.arun(url=url, config=config)
        except Exception as exc:  # pragma: no cover - browser/upstream failure
            raise PageLoadError(f"Crawl4AI could not load {url!r}: {exc}") from exc
        if not getattr(result, "success", False):
            detail = getattr(result, "error_message", None) or "unknown crawler error"
            raise PageLoadError(f"Crawl4AI could not load {url!r}: {detail}")
        html = getattr(result, "html", None)
        if not isinstance(html, str) or not html:
            raise PageLoadError(f"Crawl4AI returned no HTML for {url!r}")
        final_url = getattr(result, "redirected_url", None) or getattr(result, "url", None) or url
        return RenderedPage(
            url=final_url,
            html=html,
            structured_data=_structured(getattr(result, "extracted_content", None)),
            links=_links(final_url, getattr(result, "links", None)),
            images=_images(final_url, getattr(result, "media", None)),
            mhtml=getattr(result, "mhtml", None),
            structure_fingerprint=getattr(result, "head_fingerprint", None),
        )

    def extract(self, page: RenderedPage, schema: ExtractionSchema) -> tuple[dict[str, Any], ...]:
        """Apply a saved Crawl4AI schema to already-rendered HTML."""

        from crawl4ai import JsonCssExtractionStrategy

        return tuple(JsonCssExtractionStrategy(schema).extract(page.url, page.html))

    async def generate_schema(self, page: RenderedPage) -> ExtractionSchema:
        """Generate and refine a candidate CSS schema from one rendered page."""

        if not self._schema_model or not self._api_key:
            raise ExtractionDependencyError(
                "no reusable schema exists and Crawl4AI schema generation is not configured"
            )
        from crawl4ai import JsonCssExtractionStrategy, LLMConfig

        config = LLMConfig(provider=self._schema_model, api_token=self._api_key)
        schema = await asyncio.to_thread(
            JsonCssExtractionStrategy.generate_schema,
            html=page.html,
            query=_SCHEMA_QUERY,
            target_json_example=json.dumps(_TARGET_JSON),
            llm_config=config,
            validate=True,
            max_refinements=2,
        )
        if not isinstance(schema, dict):
            raise PageLoadError("Crawl4AI did not generate a valid extraction schema")
        return schema

    async def discover(self, url: str) -> tuple[SourceLink, ...]:
        """Use Crawl4AI's bounded deep crawl to find related product pages."""

        try:
            from crawl4ai import AsyncWebCrawler, BFSDeepCrawlStrategy, CrawlerRunConfig
            from crawl4ai.deep_crawling.filters import DomainFilter, FilterChain, URLPatternFilter
            from crawl4ai.deep_crawling.scorers import KeywordRelevanceScorer
        except ImportError as exc:  # pragma: no cover - required production dependency
            raise ExtractionDependencyError("Crawl4AI is not installed") from exc
        host = urlsplit(url).hostname or ""
        strategy = BFSDeepCrawlStrategy(
            max_depth=1,
            max_pages=12,
            filter_chain=FilterChain(
                [
                    DomainFilter(allowed_domains=[host]),
                    URLPatternFilter(
                        patterns=[
                            "*login*",
                            "*cart*",
                            "*privacy*",
                            "*legal*",
                            "*imprint*",
                            "*facebook*",
                            "*instagram*",
                            "*linkedin*",
                        ],
                        reverse=True,
                    ),
                ]
            ),
            url_scorer=KeywordRelevanceScorer(
                keywords=["product", "technical", "datasheet", "download", "document"]
            ),
        )
        config = CrawlerRunConfig(deep_crawl_strategy=strategy, stream=False)
        try:
            async with AsyncWebCrawler() as crawler:
                results = await crawler.arun(url=url, config=config)
        except Exception as exc:  # pragma: no cover - browser/upstream failure
            raise PageLoadError(f"Crawl4AI could not discover sources from {url!r}: {exc}") from exc
        return tuple(
            SourceLink(
                url=str(result.url),
                text=str(getattr(result, "metadata", {}).get("title") or ""),
            )
            for result in results
            if getattr(result, "success", False) and getattr(result, "url", None) != url
        )


def _structured(value: object) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, str) or not value.strip():
        return ()
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise PageLoadError("Crawl4AI returned invalid structured JSON") from exc
    records = decoded if isinstance(decoded, list) else [decoded]
    return tuple(item for item in records if isinstance(item, dict))


def _links(base_url: str, value: object) -> tuple[SourceLink, ...]:
    groups = value.values() if isinstance(value, dict) else ()
    return tuple(
        SourceLink(
            url=urljoin(base_url, str(item.get("href", ""))),
            text=str(item.get("text") or "").strip(),
            title=str(item.get("title") or "").strip(),
            media_type=item.get("mime_type") or item.get("content_type"),
        )
        for items in groups
        if isinstance(items, list)
        for item in items
        if isinstance(item, dict) and item.get("href")
    )


def _images(base_url: str, value: object) -> tuple[SourceImage, ...]:
    items = value.get("images", []) if isinstance(value, dict) else []
    return tuple(
        SourceImage(
            url=urljoin(base_url, str(item.get("src", ""))),
            alt=str(item.get("alt") or "").strip(),
            title=str(item.get("desc") or "").strip(),
            score=float(item["score"]) if item.get("score") is not None else None,
            width=int(item["width"]) if item.get("width") is not None else None,
        )
        for item in items
        if isinstance(item, dict) and item.get("src")
    )

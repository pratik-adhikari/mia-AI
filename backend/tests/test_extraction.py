"""Tests for the provider boundary used by live web extraction."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import crawl4ai
import pytest

from mia_dpp.integrations.crawl4ai import Crawl4AIPageLoader
from mia_dpp.tools.web.models import RenderedPage

PRODUCT_URL = "https://manufacturer.example/products/pg-16"


def test_rendered_page_requires_an_aware_acquisition_time() -> None:
    with pytest.raises(ValueError, match="timezone"):
        RenderedPage(
            url=PRODUCT_URL,
            html="<html></html>",
            acquired_at=datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC).replace(tzinfo=None),
        )


def test_crawl4ai_loader_converts_upstream_result_and_keeps_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCrawler:
        async def __aenter__(self) -> FakeCrawler:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def arun(self, *, url: str, config: object) -> SimpleNamespace:
            return SimpleNamespace(
                success=True,
                html="<html><h1>Product</h1></html>",
                url=url,
                redirected_url="https://manufacturer.example/products/final",
                extracted_content='[{"name":"Product"}]',
                links={"internal": []},
                media={"images": []},
                mhtml="snapshot",
                head_fingerprint="head-v1",
            )

    monkeypatch.setattr(crawl4ai, "AsyncWebCrawler", FakeCrawler)

    page = asyncio.run(
        Crawl4AIPageLoader().load(
            PRODUCT_URL,
            {"name": "product", "baseSelector": "body", "fields": []},
        )
    )

    assert page.url == "https://manufacturer.example/products/final"
    assert page.html == "<html><h1>Product</h1></html>"
    assert page.mhtml == "snapshot"
    assert page.structure_fingerprint == "head-v1"
    assert page.structured_data == ({"name": "Product"},)


def test_crawl4ai_loader_uses_bounded_native_deep_crawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    class FakeCrawler:
        async def __aenter__(self) -> FakeCrawler:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def arun(self, *, url: str, config: object) -> list[SimpleNamespace]:
            captured.append(config)
            return [
                SimpleNamespace(success=True, url=url, metadata={}),
                SimpleNamespace(
                    success=True,
                    url="https://manufacturer.example/products/pg-16/datasheet",
                    metadata={"title": "Technical data"},
                ),
            ]

    monkeypatch.setattr(crawl4ai, "AsyncWebCrawler", FakeCrawler)

    links = asyncio.run(Crawl4AIPageLoader().discover(PRODUCT_URL))

    assert [link.url for link in links] == ["https://manufacturer.example/products/pg-16/datasheet"]
    strategy = captured[0].deep_crawl_strategy
    assert strategy.max_depth == 1
    assert strategy.max_pages == 12

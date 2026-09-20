"""Crawl4AI implementation of MIA's provider-neutral page loader."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from mia_dpp.domain.evidence import ExtractedAsset, ExtractedProductPage
from mia_dpp.tools.web.models import (
    ExtractionDependencyError,
    PageLoadError,
    RenderedPage,
    SourceLink,
)

# Product sites commonly lazy-load tab contents. This generic browser script uses only
# accessibility/HTML semantics, records assets before tabs replace their DOM, and deliberately
# contains no manufacturer selectors.
_EXPAND_STANDARD_PAGE_CONTROLS = r"""
const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
const observedAssets = [];
const observedUrls = new Set();
const collectAssets = (root, contextPath = []) => {
  // Navigation chrome produces logos and unrelated links, so only retain content assets.
  root.querySelectorAll('img').forEach(image => {
    if (image.closest('nav, header, footer, aside, form')) return;
    const url = image.currentSrc || image.src;
    if (!url || !url.startsWith('http') || observedUrls.has(url)) return;
    observedUrls.add(url);
    observedAssets.push({
      url,
      kind: 'image',
      label: image.title || image.alt || 'Image',
      contextPath,
    });
  });
  root.querySelectorAll('a[href]').forEach(link => {
    const url = link.href;
    if (!url || !/\.(pdf|docx?|xlsx?|csv|zip|step|stp|dxf)(?:[?#]|$)/i.test(url)
        || observedUrls.has(url)) return;
    const text = link.textContent.trim();
    const filename = decodeURIComponent(new URL(url).pathname.split('/').pop() || 'Document');
    observedUrls.add(url);
    observedAssets.push({
      url,
      kind: 'document',
      // Some download buttons show only a byte size; the URL filename is more useful to humans.
      label: /^\s*(?:\d+(?:[.,]\d+)?\s*[kmgt]?b\s*)+$/i.test(text)
        ? filename
        : text || link.title || filename,
      contextPath,
    });
  });
  // Public previews commonly expose their real file through an iframe/embed instead of a link.
  root.querySelectorAll('iframe[src], embed[src], object[data]').forEach(element => {
    const candidate = element.getAttribute('src') || element.getAttribute('data');
    if (!candidate) return;
    const url = new URL(candidate, location.href).href.split('#')[0];
    if (!/\.(pdf|docx?|xlsx?|csv|zip|step|stp|dxf)(?:[?#]|$)/i.test(url)
        || observedUrls.has(url)) return;
    observedUrls.add(url);
    observedAssets.push({
      url,
      kind: 'document',
      label: element.title || element.getAttribute('aria-label') || 'Document',
      contextPath,
    });
  });
};
collectAssets(document);
// ARIA tabs are the portable signal for hidden/lazy product sections across different sites.
const tabs = Array.from(document.querySelectorAll('[role="tab"][aria-controls]'))
  .filter((tab, index, all) => all.findIndex(other =>
    other.getAttribute('aria-controls') === tab.getAttribute('aria-controls')) === index);
for (const tab of tabs) {
  const targetId = tab.getAttribute('aria-controls');
  const panel = targetId ? document.getElementById(targetId) : null;
  if (!panel) continue;
  const contentSelector = 'table, dl, li, p, a[href], img, video, source';
  const alreadyLoaded = panel.querySelector(contentSelector) !== null;
  tab.click();
  // Bound the wait at ten seconds so a broken widget cannot hang an extraction indefinitely.
  if (!alreadyLoaded) {
    for (let attempt = 0; attempt < 40; attempt += 1) {
      await sleep(250);
      if (panel.querySelector(contentSelector) && panel.getAttribute('aria-busy') !== 'true') break;
    }
  }
  collectAssets(panel, [tab.textContent.trim()].filter(Boolean));
}
// Native details elements need no site-specific click logic.
document.querySelectorAll('details').forEach(element => { element.open = true; });
// Open a bounded number of standard dialogs so public document previews become observable.
// Authentication forms remain untouched: only already-rendered iframe/embed URLs are collected.
const dialogTriggers = Array.from(document.querySelectorAll('button[aria-haspopup="dialog"]'))
  .slice(0, 40);
for (const trigger of dialogTriggers) {
  const container = trigger.closest('article, li, section');
  const heading = container?.querySelector('h1, h2, h3, h4, h5, h6')?.textContent.trim();
  trigger.click();
  let dialog = null;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    await sleep(100);
    dialog = document.querySelector('[role="dialog"]');
    if (dialog) break;
  }
  if (!dialog) continue;
  collectAssets(dialog, [heading, trigger.textContent.trim()].filter(Boolean));
  const buttons = Array.from(dialog.querySelectorAll('button'));
  const close = buttons.find(button =>
    /^(close|dismiss|cancel|schließen)$/i.test(button.textContent.trim())
    || /^(close|dismiss|cancel)$/i.test(button.getAttribute('aria-label') || ''));
  if (close) {
    close.click();
  } else {
    document.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'Escape', code: 'Escape', bubbles: true,
    }));
  }
  // Wait for animated dialog teardown so its overlay cannot swallow the next trigger click.
  for (let attempt = 0; attempt < 20 && document.contains(dialog); attempt += 1) {
    await sleep(100);
  }
}
// Later tab clicks can remove earlier assets from the DOM. Archive their URLs in the retained
// rendered HTML so provenance validation can prove every structured URL came from the page.
const archive = document.createElement('div');
archive.hidden = true;
archive.setAttribute('data-mia-observed-assets', '');
observedAssets.forEach(asset => {
  const element = document.createElement(asset.kind === 'image' ? 'img' : 'a');
  if (asset.kind === 'image') {
    element.src = asset.url;
    element.alt = asset.label;
  } else {
    element.href = asset.url;
    element.textContent = asset.label;
  }
  archive.appendChild(element);
});
document.body.appendChild(archive);
return observedAssets;
"""


class Crawl4AIPageLoader:
    """Render public pages through Crawl4AI for ``WebExtractionTool``.

    ``Mia`` injects this concrete integration through the provider-neutral
    ``PageLoader`` boundary; it returns HTML and the final redirected URL.
    """

    def __init__(self, *, model: str | None = None, api_token: str | None = None) -> None:
        """Enable typed semantic extraction only when both model and credential are configured."""

        self._model = model
        self._api_token = api_token

    async def load(self, url: str) -> RenderedPage:
        """Render one URL or raise a web-tool error the agent/API can handle."""

        return await self._load(url, semantic=True)

    async def load_source(self, url: str) -> RenderedPage:
        """Render an explored source without repeating the expensive semantic model request."""

        return await self._load(url, semantic=False)

    async def _load(self, url: str, *, semantic: bool) -> RenderedPage:
        """Use one Crawl4AI acquisition path for seed and LLM-selected source pages."""

        try:
            from crawl4ai import AsyncWebCrawler
        except ImportError as exc:  # pragma: no cover - optional installation
            raise ExtractionDependencyError(
                "Crawl4AI is not installed; install the crawl runtime to load live pages"
            ) from exc

        try:
            try:
                from crawl4ai import (
                    CacheMode,
                    CrawlerRunConfig,
                    LLMConfig,
                    LLMExtractionStrategy,
                )
            except ImportError:
                # Keep compatibility with Crawl4AI installations predating run configurations.
                run_config = None
            else:
                extraction = None
                if semantic and self._model and self._api_token:
                    # Crawl4AI owns schema enforcement; MIA owns the provider-neutral result model.
                    extraction = LLMExtractionStrategy(
                        llm_config=LLMConfig(
                            provider=f"openrouter/{self._model}",
                            api_token=self._api_token,
                            temperature=0,
                        ),
                        schema=ExtractedProductPage.model_json_schema(),
                        extraction_type="schema",
                        input_format="markdown",
                        apply_chunking=False,
                        force_json_response=True,
                        instruction=(
                            "Extract all useful facts about the product into sections. Preserve "
                            "the visible tab, heading, subsection, component, variant, table, or "
                            "card hierarchy in contextPath. Copy labels and values faithfully "
                            "without HTML or Markdown syntax. Keep repeated labels separate when "
                            "their context differs. Each visible heading or subheading must "
                            "become its own hierarchy level, never a property label. For bold "
                            "list items, use the bold text as the property label and its following "
                            "explanation as the value. Include product-relevant images and "
                            "directly linked technical documents with their exact observed URLs "
                            "and hierarchy. Ignore navigation, cookie, footer, legal, social, "
                            "cart, and unrelated-product content. Do not invent facts or URLs. "
                            "Keep visible empty product tabs as empty sections."
                        ),
                    )
                # Rendering, scrolling, generic tab expansion, and extraction happen in one crawl.
                run_config = CrawlerRunConfig(
                    extraction_strategy=extraction,
                    cache_mode=CacheMode.BYPASS,
                    wait_until="networkidle",
                    scan_full_page=True,
                    js_code=_EXPAND_STANDARD_PAGE_CONTROLS,
                    delay_before_return_html=0.5,
                    remove_overlay_elements=True,
                    remove_consent_popups=True,
                    excluded_tags=["nav", "footer", "aside", "form"],
                )

            async with AsyncWebCrawler() as crawler:
                result = (
                    await crawler.arun(url=url, config=run_config)
                    if run_config is not None
                    else await crawler.arun(url=url)
                )
        except Exception as exc:  # pragma: no cover - browser/upstream failure
            raise PageLoadError(f"Crawl4AI could not load {url!r}: {exc}") from exc

        if not getattr(result, "success", False):
            detail = getattr(result, "error_message", None) or "unknown crawler error"
            raise PageLoadError(f"Crawl4AI could not load {url!r}: {detail}")
        html = getattr(result, "html", None)
        if not isinstance(html, str) or not html:
            raise PageLoadError(f"Crawl4AI returned no HTML for {url!r}")
        final_url = getattr(result, "redirected_url", None) or getattr(result, "url", None)
        markdown_result = getattr(result, "markdown", None)
        markdown = getattr(markdown_result, "raw_markdown", "")
        # JavaScript results preserve assets that disappeared as later tabs were activated.
        observed_assets = self._observed_assets(getattr(result, "js_execution_result", None))
        extracted_content = self._include_observed_assets(
            getattr(result, "extracted_content", None),
            observed_assets,
        )
        return RenderedPage(
            url=final_url if isinstance(final_url, str) and final_url else url,
            html=html,
            markdown=markdown if isinstance(markdown, str) else "",
            extracted_content=extracted_content,
            observed_assets=observed_assets,
        )

    @staticmethod
    def _observed_assets(execution_result: object) -> tuple[ExtractedAsset, ...]:
        """Validate Crawl4AI's JavaScript return value before it enters trusted domain state."""

        if not isinstance(execution_result, dict):
            return ()
        results = execution_result.get("results")
        if not isinstance(results, list) or not results or not isinstance(results[0], list):
            return ()
        return tuple(ExtractedAsset.model_validate(item) for item in results[0])

    @staticmethod
    def _include_observed_assets(
        extracted_content: str | None,
        observed_assets: tuple[ExtractedAsset, ...],
    ) -> str | None:
        """Merge browser-observed assets into LLM output without duplicating URLs."""

        if not extracted_content:
            return None
        payload = json.loads(extracted_content)
        if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
            return extracted_content
        existing = payload[0].get("assets", [])
        observed_by_url = {asset.url: asset for asset in observed_assets}
        assets: list[ExtractedAsset] = []
        for item in existing:
            # Structured models occasionally return an empty optional-looking label despite the
            # schema constraint. Reuse browser-grounded metadata without weakening URL validation.
            if isinstance(item, dict) and not str(item.get("label") or "").strip():
                observed = observed_by_url.get(str(item.get("url") or ""))
                item = {
                    **item,
                    "label": observed.label if observed else str(item.get("kind") or "Asset").title(),
                }
            assets.append(ExtractedAsset.model_validate(item))
        known = {asset.url for asset in assets}
        for asset in observed_assets:
            if asset.url not in known:
                assets.append(asset)
                known.add(asset.url)
        payload[0]["assets"] = [asset.model_dump(mode="json", by_alias=True) for asset in assets]
        return json.dumps(payload, ensure_ascii=False)

    async def discover(self, url: str) -> tuple[SourceLink, ...]:
        """Run a small same-domain native deep crawl for related source pages."""

        try:
            from crawl4ai import AsyncWebCrawler, BFSDeepCrawlStrategy, CrawlerRunConfig
            from crawl4ai.deep_crawling.filters import DomainFilter, FilterChain, URLPatternFilter
            from crawl4ai.deep_crawling.scorers import KeywordRelevanceScorer
        except ImportError as exc:  # pragma: no cover - optional installation
            raise ExtractionDependencyError(
                "Crawl4AI is not installed; install the crawl runtime to discover sources"
            ) from exc

        host = urlsplit(url).hostname or ""
        strategy = BFSDeepCrawlStrategy(
            max_depth=2,
            max_pages=12,
            include_external=False,
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
        try:
            async with AsyncWebCrawler() as crawler:
                stream = await crawler.arun(
                    url=url,
                    # Streaming makes Crawl4AI enforce max_pages while processing a wide BFS
                    # level; its batch mode can enqueue excess depth-two links before stopping.
                    config=CrawlerRunConfig(deep_crawl_strategy=strategy, stream=True),
                )
                results = [result async for result in stream]
        except Exception as exc:  # pragma: no cover - browser/upstream failure
            raise PageLoadError(f"Crawl4AI could not discover sources from {url!r}: {exc}") from exc

        return tuple(
            SourceLink(
                url=str(result.url),
                text=str(getattr(result, "metadata", {}).get("title") or ""),
            )
            for result in results
            if getattr(result, "success", False)
            and getattr(result, "url", None)
            and str(result.url) != url
        )

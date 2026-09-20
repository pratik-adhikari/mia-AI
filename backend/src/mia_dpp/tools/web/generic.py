"""Generic extraction from unfamiliar product pages using common HTML structures."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from typing import Any

from bs4 import BeautifulSoup, Tag

from mia_dpp.domain.evidence import (
    EvidenceRecord,
    EvidenceStatus,
    ExtractedProductPage,
    ExtractedProperty,
    ExtractedSection,
    SourceLocation,
)
from mia_dpp.tools.web.models import RenderedPage

EXTRACTOR_NAME = "mia-website-fact-extractor"
EXTRACTOR_VERSION = "1"
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_JSON_LABELS = {
    "name": "Product name",
    "sku": "SKU",
    "mpn": "MPN",
    "serialNumber": "Serial number",
    "productionDate": "Production date",
    "countryOfOrigin": "Country of origin",
}
_STRUCTURAL_JSON_KEYS = frozenset({"@context", "@type", "additionalProperty"})
_REFERENCE_JSON_KEYS = frozenset({"url", "image", "logo", "sameAs"})


class WebsiteFactExtractor:
    """Retain useful labelled facts found in common product-page structures."""

    def extract(self, source: RenderedPage) -> tuple[tuple[EvidenceRecord, ...], str]:
        facts, product_name, _ = self.extract_structured(source)
        return facts, product_name

    def extract_structured(
        self, source: RenderedPage
    ) -> tuple[tuple[EvidenceRecord, ...], str, ExtractedProductPage | None]:
        """Return canonical evidence plus the readable hierarchy used to produce it.

        Crawl4AI's strict schema is preferred when configured. The existing deterministic HTML
        parser remains available for local/offline diagnostics and produces the same domain type.
        """

        if source.extracted_content:
            # Normalize exactly one validated page shape instead of interpreting arbitrary JSON.
            page = self._validated_page(source)
            facts: list[EvidenceRecord] = []
            for label, value in (
                ("Product name", page.product_name),
                ("Product type", page.product_type),
                ("Description", page.summary),
            ):
                if value:
                    self._append(
                        facts,
                        source,
                        label=label,
                        value=value,
                        method="crawl4ai_schema",
                        location=SourceLocation(excerpt=value[:1000]),
                    )
            for section in page.sections:
                context = tuple(part.strip() for part in section.context_path if part.strip())
                for prop in section.properties:
                    self._append(
                        facts,
                        source,
                        label=prop.label,
                        value=prop.value,
                        unit=prop.unit,
                        method="crawl4ai_schema",
                        context_path=context,
                        location=SourceLocation(
                            excerpt=(prop.source_excerpt or f"{prop.label}: {prop.value}")[:1000]
                        ),
                    )
            return tuple(self._deduplicate(facts)), page.product_name, page

        # Project legacy deterministic facts into the same hierarchy so downstream storage/UI
        # never needs to know which extraction mode produced the page.
        fallback_facts, product_name = self._extract_html(source)
        grouped: dict[tuple[str, ...], list[ExtractedProperty]] = {}
        for fact in fallback_facts:
            if fact.source_label and isinstance(fact.value, (str, int, float, bool)):
                grouped.setdefault(fact.context_path, []).append(
                    ExtractedProperty(
                        label=fact.source_label,
                        value=str(fact.value),
                        unit=fact.unit,
                        source_excerpt=fact.source_location.excerpt,
                    )
                )
        page = ExtractedProductPage(
            source_url=source.url,
            product_name=product_name,
            sections=tuple(
                ExtractedSection(context_path=context, properties=tuple(properties))
                for context, properties in grouped.items()
            ),
            assets=source.observed_assets,
        )
        return fallback_facts, product_name, page

    def _extract_html(self, source: RenderedPage) -> tuple[tuple[EvidenceRecord, ...], str]:
        """Extract normalized, provenance-rich evidence directly from one page."""

        soup = BeautifulSoup(source.html, "html.parser")
        facts: list[EvidenceRecord] = []

        for script_index, script in enumerate(soup.select('script[type="application/ld+json"]')):
            raw = script.string if script.string is not None else script.get_text()
            try:
                document = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            selector = f"script[type='application/ld+json'] (match {script_index + 1})"
            for entity, pointer in self._product_entities(document):
                self._append_json_product(facts, source, entity, pointer, selector)

        heading = soup.find("h1")
        if heading is not None:
            value = self._text(heading.get_text(" ", strip=True))
            self._append(
                facts,
                source,
                label="Product name",
                value=value,
                method="css",
                location=SourceLocation(selector="h1", excerpt=value[:240]),
            )

        title = soup.find("title")
        if title is not None:
            value = self._text(title.get_text(" ", strip=True))
            self._append(
                facts,
                source,
                label="Page title",
                value=value,
                method="html_metadata",
                location=SourceLocation(selector="title", excerpt=value[:240]),
            )

        self._append(
            facts,
            source,
            label="Product page URL",
            value=source.url,
            method="source_metadata",
            location=SourceLocation(excerpt=source.url[:240]),
        )

        for index, term in enumerate(soup.find_all("dt")):
            value_node = term.find_next_sibling("dd")
            if value_node is not None:
                self._append_html_pair(
                    facts,
                    source,
                    term,
                    value_node,
                    SourceLocation(selector=f"dt:nth-of-type({index + 1})"),
                    context_path=self._heading_context(term),
                )

        for table_index, table in enumerate(soup.find_all("table")):
            caption = table.find("caption")
            table_name = (
                self._text(caption.get_text(" ", strip=True))
                if caption is not None
                else f"table {table_index + 1}"
            )
            context = self._heading_context(table)
            if caption is not None and table_name:
                context = (*context, table_name)
            for row_index, row in enumerate(table.find_all("tr")):
                cells = row.find_all(["th", "td"], recursive=False)
                if len(cells) >= 2:
                    self._append_html_pair(
                        facts,
                        source,
                        cells[0],
                        cells[1],
                        SourceLocation(
                            selector=(
                                f"table:nth-of-type({table_index + 1}) "
                                f"tr:nth-of-type({row_index + 1})"
                            ),
                            table=table_name,
                            cell=f"row {row_index + 1}",
                        ),
                        context_path=context,
                    )

        self._append_label_value_groups(facts, source, soup)

        facts = self._deduplicate(facts)
        product_name = self._first_value(facts, "Product name", "Model", "Page title")
        return tuple(facts), (product_name or source.url)[:120]

    @staticmethod
    def _validated_page(source: RenderedPage) -> ExtractedProductPage:
        """Reject malformed output and asset URLs not grounded in retained source content."""

        payload = json.loads(source.extracted_content or "")
        if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
            raise ValueError("Crawl4AI structured extraction must return exactly one product page")
        page_data = {key: value for key, value in payload[0].items() if key != "error"}
        # Some providers still emit schema-shaped placeholders with blank label/value strings.
        # They contain no evidence, so remove only those placeholders before strict validation.
        for section in page_data.get("sections", []):
            if isinstance(section, dict) and isinstance(section.get("properties"), list):
                section["properties"] = [
                    item
                    for item in section["properties"]
                    if isinstance(item, dict)
                    and str(item.get("label") or "").strip()
                    and str(item.get("value") or "").strip()
                ]
        page_data["sourceUrl"] = source.url
        page = ExtractedProductPage.model_validate(page_data)
        unknown_assets = [
            asset.url
            for asset in page.assets
            if asset.url not in source.html and asset.url not in source.markdown
        ]
        if unknown_assets:
            raise ValueError(
                f"structured extraction returned unobserved asset URLs: {sorted(unknown_assets)}"
            )
        return page

    def _append_json_product(
        self,
        facts: list[EvidenceRecord],
        source: RenderedPage,
        entity: dict[str, Any],
        pointer: str,
        selector: str,
    ) -> None:
        for key, raw_value in entity.items():
            if key in _STRUCTURAL_JSON_KEYS or key in _REFERENCE_JSON_KEYS:
                continue
            if key == "offers" or key in {"review", "aggregateRating"}:
                continue
            label = _JSON_LABELS.get(key, self._humanize(key))
            extracted = self._json_values(raw_value)
            for occurrence, (value, unit) in enumerate(extracted):
                suffix = f"/{occurrence}" if len(extracted) > 1 else ""
                location = SourceLocation(
                    selector=selector,
                    json_pointer=f"{pointer}/{self._pointer_token(key)}{suffix}",
                    excerpt=f"{label}: {value}"[:240],
                )
                self._append(
                    facts,
                    source,
                    label=label,
                    value=value,
                    unit=unit,
                    method="json_ld",
                    location=location,
                )

                if key == "productionDate" and (year := _YEAR.search(value)):
                    self._append(
                        facts,
                        source,
                        label="Year of construction",
                        value=year.group(1),
                        method="json_ld_derived",
                        location=location.model_copy(
                            update={"excerpt": f"Derived year {year.group(1)} from {value}"}
                        ),
                    )

        additional = entity.get("additionalProperty")
        properties = [additional] if isinstance(additional, dict) else additional
        if isinstance(properties, list):
            for index, item in enumerate(properties):
                if not isinstance(item, dict):
                    continue
                label = self._scalar(item.get("name") or item.get("propertyID"))
                value = self._scalar(item.get("value"))
                unit = self._scalar(item.get("unitText") or item.get("unitCode")) or None
                if label and value:
                    self._append(
                        facts,
                        source,
                        label=label,
                        value=value,
                        unit=unit,
                        method="json_ld_additional_property",
                        location=SourceLocation(
                            selector=selector,
                            json_pointer=f"{pointer}/additionalProperty/{index}/value",
                            excerpt=f"{label}: {value}"[:240],
                        ),
                    )

    def _append_html_pair(
        self,
        facts: list[EvidenceRecord],
        source: RenderedPage,
        label_node: Any,
        value_node: Any,
        location: SourceLocation,
        *,
        context_path: tuple[str, ...] = (),
    ) -> None:
        label = self._text(label_node.get_text(" ", strip=True))
        value = self._text(value_node.get_text(" ", strip=True))
        if not label or not value or len(label) > 120 or len(value) > 2_000:
            return
        self._append(
            facts,
            source,
            label=label,
            value=value,
            method="html_label",
            location=location.model_copy(update={"excerpt": f"{label}: {value}"[:240]}),
            context_path=context_path,
        )

    def _append_label_value_groups(
        self,
        facts: list[EvidenceRecord],
        source: RenderedPage,
        soup: BeautifulSoup,
    ) -> None:
        """Read common cards/lists without relying on manufacturer CSS classes."""

        for index, node in enumerate(soup.find_all(["li", "p", "div"])):
            if not isinstance(node, Tag) or node.find(["table", "dl"]):
                continue
            direct = [child for child in node.children if isinstance(child, Tag)]
            if len(direct) == 2 and direct[0].name in {"b", "strong", "label", "span"}:
                self._append_html_pair(
                    facts,
                    source,
                    direct[0],
                    direct[1],
                    SourceLocation(selector=f"{node.name}:nth-of-type({index + 1})"),
                    context_path=self._heading_context(node),
                )
                continue
            if direct:
                continue
            text = self._text(node.get_text(" ", strip=True))
            if ":" not in text:
                continue
            label, value = (part.strip() for part in text.split(":", 1))
            if label and value and len(label) <= 120:
                self._append(
                    facts,
                    source,
                    label=label,
                    value=value,
                    method="html_label_value_text",
                    location=SourceLocation(
                        selector=f"{node.name}:nth-of-type({index + 1})",
                        excerpt=text[:240],
                    ),
                    context_path=self._heading_context(node),
                )

    @staticmethod
    def _append(
        facts: list[EvidenceRecord],
        source: RenderedPage,
        *,
        label: str,
        value: str,
        method: str,
        location: SourceLocation,
        unit: str | None = None,
        context_path: tuple[str, ...] = (),
    ) -> None:
        label = WebsiteFactExtractor._text(label)
        value = WebsiteFactExtractor._text(value)
        if not label or not value or len(label) > 120 or len(value) > 2_000:
            return
        identity = "\0".join(
            (
                WebsiteFactExtractor._source_id(source),
                label.casefold(),
                value,
                unit or "",
                *context_path,
                location.selector or "",
                location.json_pointer or "",
            )
        )
        evidence_id = f"ev-web-{hashlib.sha256(identity.encode()).hexdigest()[:24]}"
        facts.append(
            EvidenceRecord(
                id=evidence_id,
                predicate=f"source.{WebsiteFactExtractor._slug(label)}",
                source_label=label,
                value=value,
                unit=unit,
                context_path=context_path,
                source_location=location,
                extraction_method=method,
                extractor_name=EXTRACTOR_NAME,
                extractor_version=EXTRACTOR_VERSION,
                status=EvidenceStatus.OBSERVED,
                source_uri=source.url,
                source_content_sha256=source.content_sha256,
                acquired_at=source.acquired_at,
            )
        )

    @classmethod
    def _json_values(cls, value: Any) -> list[tuple[str, str | None]]:
        if isinstance(value, (str, int, float, bool)):
            return [(cls._text(str(value)), None)]
        if isinstance(value, list):
            return [item for entry in value for item in cls._json_values(entry)]
        if not isinstance(value, dict):
            return []
        direct = cls._scalar(value.get("value"))
        if direct:
            unit = cls._scalar(value.get("unitText") or value.get("unitCode")) or None
            return [(direct, unit)]
        named = cls._scalar(value.get("name"))
        return [(named, None)] if named else []

    @classmethod
    def _product_entities(
        cls,
        value: Any,
        pointer: str = "",
    ) -> Iterable[tuple[dict[str, Any], str]]:
        if isinstance(value, list):
            for index, item in enumerate(value):
                yield from cls._product_entities(item, f"{pointer}/{index}")
            return
        if not isinstance(value, dict):
            return
        declared = value.get("@type")
        types = declared if isinstance(declared, list) else [declared]
        if any(
            isinstance(item, str) and item.rstrip("/").rsplit("/", 1)[-1].casefold() == "product"
            for item in types
        ):
            yield value, pointer
        for key, item in value.items():
            if key not in {"@context"} and isinstance(item, (dict, list)):
                yield from cls._product_entities(
                    item,
                    f"{pointer}/{cls._pointer_token(key)}",
                )

    @staticmethod
    def _deduplicate(facts: list[EvidenceRecord]) -> list[EvidenceRecord]:
        seen: set[tuple[tuple[str, ...], str, str, str]] = set()
        result: list[EvidenceRecord] = []
        for fact in facts:
            key = (
                fact.context_path,
                (fact.source_label or fact.predicate).casefold(),
                str(fact.value).casefold(),
                fact.unit or "",
            )
            if key not in seen:
                seen.add(key)
                result.append(fact)
        return result

    @classmethod
    def _heading_context(cls, node: Tag) -> tuple[str, ...]:
        """Return the active h2-h6 hierarchy at a source node."""

        levels: dict[int, str] = {}
        headings = reversed(node.find_all_previous(re.compile(r"^h[1-6]$")))
        for heading in headings:
            if not isinstance(heading, Tag) or heading.name == "h1":
                continue
            level = int(heading.name[1])
            text = cls._text(heading.get_text(" ", strip=True))
            if not text:
                continue
            levels = {key: value for key, value in levels.items() if key < level}
            levels[level] = text
        return tuple(levels[key] for key in sorted(levels))

    @staticmethod
    def _first_value(facts: list[EvidenceRecord], *labels: str) -> str:
        wanted = [label.casefold() for label in labels]
        for label in wanted:
            for fact in facts:
                if (fact.source_label or fact.predicate).casefold() == label:
                    return str(fact.value)
        return ""

    @staticmethod
    def _humanize(value: str) -> str:
        words = _CAMEL_BOUNDARY.sub(" ", value).replace("_", " ")
        return WebsiteFactExtractor._text(words).capitalize()

    @staticmethod
    def _pointer_token(value: str) -> str:
        return value.replace("~", "~0").replace("/", "~1")

    @staticmethod
    def _scalar(value: Any) -> str:
        if isinstance(value, (str, int, float, bool)):
            return WebsiteFactExtractor._text(str(value))
        return ""

    @staticmethod
    def _text(value: str) -> str:
        return " ".join(value.split()).strip()

    @staticmethod
    def _source_id(source: RenderedPage) -> str:
        return f"source-web-{source.content_sha256[:24]}"

    @staticmethod
    def _slug(label: str) -> str:
        return re.sub(r"[^a-z0-9]+", ".", label.casefold()).strip(".") or "fact"

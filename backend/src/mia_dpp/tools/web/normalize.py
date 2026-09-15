"""Project Crawl4AI structured JSON directly into MIA evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from typing import Any

from mia_dpp.domain.evidence import EvidenceRecord, EvidenceStatus, SourceLocation
from mia_dpp.tools.web.models import ExtractionSchema, RenderedPage

_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")
_JSON_LABELS = {
    "name": "Product name",
    "sku": "SKU",
    "mpn": "MPN",
    "model": "Model",
    "serialNumber": "Serial number",
    "productionDate": "Production date",
    "countryOfOrigin": "Country of origin",
}


def normalize(
    page: RenderedPage, schema: ExtractionSchema
) -> tuple[tuple[EvidenceRecord, ...], str]:
    """Create one evidence record per fact in Crawl4AI's canonical JSON shape."""

    facts: list[EvidenceRecord] = []
    product_name = ""
    for record_index, record in enumerate(page.structured_data):
        product = record.get("product")
        if isinstance(product, dict):
            product_name = _text(product.get("name")) or product_name
        product_name = _text(record.get("productName")) or _text(record.get("name")) or product_name
        for key, items in record.items():
            if key.casefold().endswith("facts") and isinstance(items, list):
                for item_index, item in enumerate(items):
                    if isinstance(item, dict):
                        _add_fact(
                            facts,
                            page,
                            item,
                            record_path=(record_index, item_index),
                            selector=_selector(schema, key, "value"),
                        )
        for section_index, section in enumerate(_dicts(record.get("sections"))):
            context = tuple(filter(None, (_text(section.get("name")),)))
            _add_fact(
                facts,
                page,
                section,
                record_path=(record_index, section_index),
                selector=_selector(schema, "sections", "value"),
            )
            for item_index, item in enumerate(_dicts(section.get("properties"))):
                _add_fact(
                    facts,
                    page,
                    item,
                    context=context,
                    record_path=(record_index, section_index, item_index),
                    selector=_selector(schema, "sections", "properties", "value"),
                )
        for entity, pointer in _products(record.get("jsonLd")):
            for label, value, unit, suffix in _json_facts(entity):
                location = f"{pointer}{suffix}"
                _add(
                    facts,
                    page,
                    label,
                    value,
                    unit,
                    record_path=(record_index,),
                    selector=_selector(schema, "jsonLd"),
                    json_pointer=location,
                )
                if label == "Production date" and (year := _YEAR.search(value)):
                    _add(
                        facts,
                        page,
                        "Year of construction",
                        year.group(1),
                        None,
                        record_path=(record_index,),
                        selector=_selector(schema, "jsonLd"),
                        json_pointer=location,
                        method="crawl4ai_json_ld_derived",
                    )
    if product_name:
        _add(facts, page, "Product name", product_name, None)
    _add(facts, page, "Product page URL", page.url, None)
    unique = {
        (item.context_path, item.source_label, str(item.value), item.unit): item for item in facts
    }
    evidence = tuple(unique.values())
    if not product_name:
        product_name = next(
            (
                str(item.value)
                for item in evidence
                if item.source_label in {"Product name", "Model"}
            ),
            page.url,
        )
    return evidence, product_name[:120]


def _add_fact(
    facts: list[EvidenceRecord],
    page: RenderedPage,
    item: dict[str, Any],
    *,
    context: tuple[str, ...] = (),
    record_path: tuple[int, ...],
    selector: str | None,
) -> None:
    label = _text(item.get("label") or item.get("name")).rstrip(":")
    value = _text(item.get("value"))
    unit = _text(item.get("unit") or item.get("unitText") or item.get("unitCode")) or None
    if label and value:
        _add(facts, page, label, value, unit, context, record_path, selector)


def _add(
    facts: list[EvidenceRecord],
    page: RenderedPage,
    label: str,
    value: str,
    unit: str | None,
    context: tuple[str, ...] = (),
    record_path: tuple[int, ...] = (),
    selector: str | None = None,
    json_pointer: str | None = None,
    *,
    method: str = "crawl4ai_structured_json",
) -> None:
    if len(label) > 120 or len(value) > 2_000:
        return
    identity = "\0".join((page.content_sha256, *context, label.casefold(), value, unit or ""))
    facts.append(
        EvidenceRecord(
            id=f"ev-web-{hashlib.sha256(identity.encode()).hexdigest()[:24]}",
            predicate=(
                f"source.{re.sub(r'[^a-z0-9]+', '.', label.casefold()).strip('.') or 'fact'}"
            ),
            source_label=label,
            value=value,
            unit=unit,
            context_path=context,
            source_location=SourceLocation(
                selector=selector,
                base_selector=page.base_selector,
                schema_id=page.schema_id,
                record_path=record_path,
                json_pointer=json_pointer,
                excerpt=f"{label}: {value}"[:240],
            ),
            extraction_method=method,
            extractor_name="crawl4ai-json",
            extractor_version="1",
            status=EvidenceStatus.OBSERVED,
            source_uri=page.url,
            source_content_sha256=page.content_sha256,
            acquired_at=page.acquired_at,
        )
    )


def _products(value: object, pointer: str = "") -> Iterator[tuple[dict[str, Any], str]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _products(item, f"{pointer}/{index}")
    elif isinstance(value, dict):
        kinds = value.get("@type")
        if "Product" in ([kinds] if isinstance(kinds, str) else kinds or []):
            yield value, pointer
        for key, item in value.items():
            if key != "@context" and isinstance(item, (dict, list)):
                yield from _products(item, f"{pointer}/{key}")


def _json_facts(entity: dict[str, Any]) -> Iterator[tuple[str, str, str | None, str]]:
    ignored = {
        "@context",
        "@type",
        "additionalProperty",
        "url",
        "image",
        "logo",
        "offers",
    }
    for key, raw in entity.items():
        if key in ignored or isinstance(raw, list):
            continue
        value = _text(raw.get("name") if isinstance(raw, dict) else raw)
        if value:
            label = _JSON_LABELS.get(
                key,
                re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", key).capitalize(),
            )
            yield label, value, None, f"/{key}"
    for index, item in enumerate(_dicts(entity.get("additionalProperty"))):
        label = _text(item.get("name") or item.get("label")).rstrip(":")
        value = _text(item.get("value"))
        if label and value:
            unit = _text(item.get("unitText") or item.get("unitCode")) or None
            yield label, value, unit, f"/additionalProperty/{index}/value"


def _selector(schema: ExtractionSchema, *path: str) -> str | None:
    fields, selector = schema.get("fields", []), None
    for name in path:
        field = next((item for item in fields if item.get("name") == name), None)
        if field is None:
            break
        selector, fields = field.get("selector") or selector, field.get("fields", [])
    return selector


def _dicts(value: object) -> Iterator[dict[str, Any]]:
    return (
        (item for item in value if isinstance(item, dict)) if isinstance(value, list) else iter(())
    )


def _text(value: object) -> str:
    return " ".join(str(value).split()) if value is not None else ""

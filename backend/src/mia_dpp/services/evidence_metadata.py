"""Deterministic product metadata helpers derived from retained evidence."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from mia_dpp.domain.evidence import ProductKnowledgePackage


def evidence_text(package: ProductKnowledgePackage, *labels: str) -> str | None:
    """Return the first scalar evidence value whose visible label matches."""

    wanted = {item.casefold() for item in labels}
    for record in package.evidence:
        label = (record.source_label or record.predicate).casefold().strip()
        if label in wanted and isinstance(record.value, (str, int, float)):
            return str(record.value)
    return None


def product_image_url(package: ProductKnowledgePackage) -> str | None:
    """Prefer explicit page metadata; fall back to schema.org Product.image."""

    for source in package.acquired_sources:
        soup = BeautifulSoup(source.rendered_html, "html.parser")
        for selector, attribute in (
            ('meta[property="og:image"]', "content"),
            ('meta[name="twitter:image"]', "content"),
        ):
            node = soup.select_one(selector)
            value = node.get(attribute) if node else None
            if isinstance(value, str) and value.strip():
                return urljoin(source.final_url, value.strip())
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                payload = json.loads(script.string or "")
            except (TypeError, json.JSONDecodeError):
                continue
            for item in _json_objects(payload):
                types = item.get("@type")
                type_names = {types} if isinstance(types, str) else set(types or ())
                if "Product" not in type_names:
                    continue
                image = item.get("image")
                if isinstance(image, list):
                    image = next((value for value in image if isinstance(value, str)), None)
                if isinstance(image, dict):
                    image = image.get("url") or image.get("contentUrl")
                if isinstance(image, str) and image.strip():
                    return urljoin(source.final_url, image.strip())
    return None


def _json_objects(value: object) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        graph = value.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                if isinstance(item, dict):
                    yield item
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item

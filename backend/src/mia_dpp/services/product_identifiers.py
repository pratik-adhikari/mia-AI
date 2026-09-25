"""Extract safe product/instance/classification identifiers from retained evidence."""

from __future__ import annotations

import hashlib
import re

from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.product import ProductIdentifier, ProductIdentifierRole

_TOKEN = re.compile(r"[^a-z0-9]+")


def discover_product_identifiers(
    package: ProductKnowledgePackage,
    *,
    manufacturer: str | None,
) -> tuple[ProductIdentifier, ...]:
    """Classify identifiers conservatively; classifications are never product identity keys."""

    found: list[ProductIdentifier] = []
    seen: set[tuple[str, str | None, str, ProductIdentifierRole]] = set()
    manufacturer_namespace = _normalize(manufacturer) if manufacturer else None

    for evidence in package.evidence:
        label = _normalize(evidence.source_label or evidence.predicate)
        value = str(evidence.value).strip()
        if not value:
            continue

        scheme: str | None = None
        namespace: str | None = None
        role: ProductIdentifierRole | None = None

        if label in {"gtin", "ean", "ean 13", "global trade item number"}:
            scheme = "gtin"
            role = ProductIdentifierRole.IDENTITY
        elif label in {"manufacturer part number", "manufacturer product id", "mpn"}:
            scheme = "manufacturer_part_number"
            namespace = manufacturer_namespace
            role = ProductIdentifierRole.IDENTITY if namespace else None
        elif label in {"manufacturer article number", "article number", "material number"}:
            scheme = "manufacturer_article_number"
            namespace = manufacturer_namespace
            role = ProductIdentifierRole.IDENTITY if namespace else None
        elif label in {"serial number", "serial"}:
            scheme = "serial_number"
            namespace = manufacturer_namespace
            role = ProductIdentifierRole.INSTANCE
        elif "eclass" in label:
            scheme = "eclass_irdi" if "irdi" in label else "eclass_classification"
            role = ProductIdentifierRole.CLASSIFICATION

        if scheme is None or role is None:
            continue
        normalized = _normalize_identifier(value)
        identity = (scheme, namespace, normalized, role)
        if not normalized or identity in seen:
            continue
        seen.add(identity)
        digest = hashlib.sha256(
            "\0".join(
                (package.product_id, scheme, namespace or "", normalized, role.value)
            ).encode()
        ).hexdigest()[:24]
        found.append(
            ProductIdentifier(
                id=f"product-identifier-{digest}",
                product_id=package.product_id,
                scheme=scheme,
                value=value,
                normalized_value=normalized,
                namespace=namespace,
                role=role,
                source_evidence_id=evidence.id,
                verified=evidence.status.value == "verified",
            )
        )
    return tuple(found)


def _normalize(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(_TOKEN.sub(" ", value.casefold()).split())


def _normalize_identifier(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())

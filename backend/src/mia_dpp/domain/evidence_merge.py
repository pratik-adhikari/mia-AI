"""Architecture-neutral helpers for combining extracted product evidence."""

from __future__ import annotations

from mia_dpp.domain.evidence import ProductKnowledgePackage


def merge_packages(
    existing: ProductKnowledgePackage,
    incoming: ProductKnowledgePackage,
    *,
    preserve_existing: bool = False,
) -> ProductKnowledgePackage:
    """Merge two knowledge packages while preserving deterministic evidence identity."""

    sources = {item.id: item for item in existing.acquired_sources}
    sources.update({item.id: item for item in incoming.acquired_sources})

    evidence = {item.id: item for item in existing.evidence}
    if preserve_existing:
        evidence = {item.id: item for item in incoming.evidence} | evidence
    else:
        evidence.update({item.id: item for item in incoming.evidence})

    return ProductKnowledgePackage(
        product_id=existing.product_id,
        product_name=existing.product_name or incoming.product_name,
        source_artifact_ids=tuple(
            dict.fromkeys((*existing.source_artifact_ids, *incoming.source_artifact_ids))
        ),
        acquired_sources=tuple(sources.values()),
        source_failures=(*existing.source_failures, *incoming.source_failures),
        extracted_pages=(*existing.extracted_pages, *incoming.extracted_pages),
        evidence=tuple(evidence.values()),
    )

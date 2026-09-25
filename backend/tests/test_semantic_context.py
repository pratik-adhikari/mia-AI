"""Deterministic multi-scope semantic context tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from mia_dpp.domain.evidence import (
    EvidenceRecord,
    EvidenceStatus,
    ProductKnowledgePackage,
    SourceLocation,
)
from mia_dpp.normalization import normalize_package
from mia_dpp.semantic import ContextScope, build_context_views


def _record(
    identifier: str,
    label: str,
    value: str,
    context: tuple[str, ...],
) -> EvidenceRecord:
    return EvidenceRecord(
        id=identifier,
        predicate=f"source.{identifier}",
        source_label=label,
        value=value,
        context_path=context,
        source_uri="https://manufacturer.example/product",
        source_content_sha256=hashlib.sha256(b"fixture").hexdigest(),
        source_location=SourceLocation(excerpt=f"{label}: {value}"),
        extraction_method="fixture",
        extractor_name="fixture",
        extractor_version="1",
        status=EvidenceStatus.OBSERVED,
        acquired_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_context_views_preserve_hierarchy_and_never_merge_evidence() -> None:
    package = ProductKnowledgePackage(
        product_id="product-test",
        product_name="Robot",
        evidence=(
            _record(
                "ev-1",
                "Repeat accuracy",
                "+/-0.03 mm",
                ("Technical Specifications", "Motion Performance"),
            ),
            _record(
                "ev-2",
                "Max velocity",
                "2 m/s",
                ("Technical Specifications", "Motion Performance"),
            ),
            _record(
                "ev-3",
                "Voltage",
                "48 V",
                ("Technical Specifications", "Electrical"),
            ),
        ),
    )
    views = build_context_views(package, normalize_package(package))
    focus = [item for item in views.views if item.focus_evidence_id == "ev-1"]
    by_scope = {item.scope: item for item in focus}

    assert set(by_scope) == set(ContextScope)
    assert by_scope[ContextScope.PROPERTY].evidence_ids == ("ev-1",)
    assert by_scope[ContextScope.PARENT].context_path == ("Technical Specifications",)
    assert by_scope[ContextScope.SIBLINGS].evidence_ids == ("ev-1", "ev-2")
    assert by_scope[ContextScope.SECTION].evidence_ids == ("ev-1", "ev-2", "ev-3")
    assert by_scope[ContextScope.FULL_PRODUCT].evidence_ids == ("ev-1", "ev-2", "ev-3")
    assert package.evidence[0].value == "+/-0.03 mm"


def test_context_view_ids_are_deterministic() -> None:
    package = ProductKnowledgePackage(
        product_id="product-test",
        product_name="Robot",
        evidence=(_record("ev-1", "Payload", "12 kg", ("Technical Specifications", "Mechanical")),),
    )
    normalization = normalize_package(package)

    first = build_context_views(package, normalization)
    second = build_context_views(package, normalization)

    assert first == second

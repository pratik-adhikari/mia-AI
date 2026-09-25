"""Lossless deterministic normalization tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from mia_dpp.domain.evidence import (
    EvidenceRecord,
    EvidenceStatus,
    ProductKnowledgePackage,
    SourceLocation,
)
from mia_dpp.normalization import (
    NormalizationOrigin,
    NormalizationStatus,
    NormalizedValueKind,
    normalize_package,
)


def _record(
    identifier: str,
    value: str,
    *,
    unit: str | None = None,
    context: tuple[str, ...] = ("Technical Specifications", "Motion"),
) -> EvidenceRecord:
    return EvidenceRecord(
        id=identifier,
        predicate="source.test",
        source_label="Test property",
        value=value,
        unit=unit,
        context_path=context,
        source_uri="https://manufacturer.example/product",
        source_content_sha256=hashlib.sha256(b"fixture").hexdigest(),
        source_location=SourceLocation(excerpt=value),
        extraction_method="fixture",
        extractor_name="fixture",
        extractor_version="1",
        status=EvidenceStatus.OBSERVED,
        acquired_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _package(*records: EvidenceRecord) -> ProductKnowledgePackage:
    return ProductKnowledgePackage(
        product_id="product-test",
        product_name="Fixture",
        evidence=records,
    )


def test_normalization_is_derived_and_preserves_raw_evidence() -> None:
    source = _record("ev-1", "+/-0,03 mm")
    package = _package(source)

    report = normalize_package(package)
    normalized = report.evidence[0]

    assert package.evidence[0] is source
    assert package.evidence[0].value == "+/-0,03 mm"
    assert normalized.evidence_id == "ev-1"
    assert normalized.value.raw == "+/-0,03 mm"
    assert normalized.value.kind is NormalizedValueKind.TOLERANCE
    assert normalized.value.number == 0.03
    assert normalized.value.unit == "mm"
    assert normalized.value.qualifier == "plus_minus"
    assert normalized.origin is NormalizationOrigin.DETERMINISTIC


def test_normalization_parses_ranges_without_semantic_inference() -> None:
    report = normalize_package(_package(_record("ev-1", "10-20 V")))
    normalized = report.evidence[0]

    assert normalized.value.kind is NormalizedValueKind.RANGE
    assert normalized.value.minimum == 10
    assert normalized.value.maximum == 20
    assert normalized.value.unit == "V"
    assert normalized.label == "Test property"
    assert normalized.context_path == ("Technical Specifications", "Motion")


def test_locale_ambiguous_number_is_not_guessed() -> None:
    report = normalize_package(_package(_record("ev-1", "1,200")))
    normalized = report.evidence[0]

    assert normalized.status is NormalizationStatus.AMBIGUOUS
    assert normalized.value.raw == "1,200"
    assert normalized.value.kind is NormalizedValueKind.TEXT
    assert normalized.ambiguity_reason is not None


def test_conflicting_embedded_and_explicit_units_are_not_silently_rewritten() -> None:
    report = normalize_package(_package(_record("ev-1", "48 V", unit="A")))
    normalized = report.evidence[0]

    assert normalized.status is NormalizationStatus.AMBIGUOUS
    assert normalized.value.raw == "48 V"
    assert normalized.ambiguity_reason is not None
    assert "conflicts" in normalized.ambiguity_reason

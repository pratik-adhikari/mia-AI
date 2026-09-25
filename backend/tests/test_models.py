"""Focused invariants for MIA's Pydantic domain boundary."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from mia_dpp.domain.evidence import (
    EvidenceRecord,
    EvidenceStatus,
    ProductKnowledgePackage,
    SourceLocation,
)
from mia_dpp.domain.mappings import MappingAssessment, MappingBasis
from mia_dpp.tools.mapping.confidence import (
    MatchQuality,
    ValueFormatQuality,
    assess_mapping,
)


def evidence(identifier: str, value: str | None = "value") -> EvidenceRecord:
    return EvidenceRecord(
        id=identifier,
        predicate="product.value",
        value=value,
        source_uri="urn:test:source",
        source_content_sha256="0" * 64,
        source_location=SourceLocation(excerpt="value"),
        extraction_method="test",
        extractor_name="test",
        extractor_version="1",
        status=EvidenceStatus.OBSERVED,
        acquired_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_wire_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        SourceLocation(excerpt="value", invented=True)


def test_usable_evidence_requires_a_value() -> None:
    with pytest.raises(ValidationError, match="must contain a value"):
        evidence("missing", None)


def test_product_knowledge_rejects_duplicate_evidence_ids() -> None:
    with pytest.raises(ValidationError, match="evidence IDs must be unique"):
        ProductKnowledgePackage(
            product_id="product-1",
            product_name="Product",
            evidence=(evidence("same"), evidence("same")),
        )


def test_mapping_assessment_requires_an_explanation() -> None:
    valid = assess_mapping(
        source_label=MatchQuality.EXACT,
        value_format=ValueFormatQuality.VALID,
        semantic_match=MatchQuality.EXACT,
        destination_candidates=1,
    )
    assert valid.basis is MappingBasis.EXACT
    with pytest.raises(ValidationError, match="at least 1 character"):
        MappingAssessment(basis=MappingBasis.EXACT, review_required=False, reason="")


def test_agent_request_has_explicit_refresh_flag() -> None:
    from mia_dpp.agent.models import AgentRequest

    normal = AgentRequest(message="Import product website: https://example.com/product")
    refresh = AgentRequest(
        message="Import product website: https://example.com/product",
        refresh_requested=True,
    )

    assert normal.refresh_requested is False
    assert refresh.refresh_requested is True

"""Complete lean batch mapping and deterministic validation tests."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from pydantic_ai.models.test import TestModel

from mia_dpp.aas.requirements import build_template_index
from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.evidence import (
    EvidenceRecord,
    EvidenceStatus,
    ProductKnowledgePackage,
    SourceLocation,
)
from mia_dpp.domain.mappings import MappingResult
from mia_dpp.tools.mapping.models import (
    BatchSemanticMappingResult,
    DeterministicMappingHint,
    EvidenceMappingDecision,
)
from mia_dpp.tools.mapping.semantic import (
    PydanticBatchSemanticMapper,
    constrained_batch_output_type,
    lean_evidence,
    lean_targets,
    validate_batch_result,
)


def package() -> ProductKnowledgePackage:
    digest = hashlib.sha256(b"fixture").hexdigest()
    records = tuple(
        EvidenceRecord(
            id=f"ev-{index}",
            predicate=f"source.fact-{index}",
            source_label=label,
            value=value,
            unit=unit,
            context_path=context,
            source_uri="https://manufacturer.example/product",
            source_content_sha256=digest,
            source_location=SourceLocation(excerpt=f"{label}: {value}"),
            extraction_method="fixture",
            extractor_name="fixture",
            extractor_version="1",
            status=EvidenceStatus.OBSERVED,
            acquired_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        for index, (label, value, unit, context) in enumerate(
            (
                ("Degree of protection", "IP68", None, ("Probe",)),
                ("Degree of protection", "IP54", None, ("Housing",)),
            ),
            start=1,
        )
    )
    return ProductKnowledgePackage(
        product_id="product-fixture",
        product_name="Fixture",
        evidence=records,
    )


def inventory():
    repository = OfficialTemplateRepository()
    return build_template_index(
        (repository.load("digital_nameplate"), repository.load("technical_data"))
    )


def unmapped_result() -> BatchSemanticMappingResult:
    return BatchSemanticMappingResult(
        decisions=tuple(
            EvidenceMappingDecision(
                evidence_id=item.id,
                status="unmapped",
                reason="No fixed official target is suitable.",
            )
            for item in package().evidence
        )
    )


def test_lean_projections_include_every_fact_and_complete_target_semantics() -> None:
    evidence = lean_evidence(package())
    targets = lean_targets(inventory())

    assert [item.id for item in evidence] == ["ev-1", "ev-2"]
    assert evidence[0].context == ("Probe",)
    assert evidence[1].context == ("Housing",)
    assert targets
    assert all(item.id.startswith("req-") for item in targets)
    assert all(item.template and item.path and item.model_type for item in targets)
    assert any(item.required for item in targets)
    assert any(item.conditional for item in targets)


@pytest.mark.parametrize("failure", ["unknown", "duplicate", "missing", "target", "alternative"])
def test_batch_result_rejects_invalid_complete_accounting(failure: str) -> None:
    evidence = lean_evidence(package())
    targets = lean_targets(inventory())
    decisions = list(unmapped_result().decisions)
    if failure == "unknown":
        decisions[0] = decisions[0].model_copy(update={"evidence_id": "ev-invented"})
    elif failure == "duplicate":
        decisions[1] = decisions[1].model_copy(update={"evidence_id": decisions[0].evidence_id})
    elif failure == "missing":
        decisions.pop()
    elif failure == "target":
        decisions[0] = EvidenceMappingDecision(
            evidence_id="ev-1",
            status="mapped",
            requirement_id="req-000000000000000000000000",
            reason="Invented target.",
        )
    else:
        decisions[0] = EvidenceMappingDecision(
            evidence_id="ev-1",
            status="uncertain",
            alternative_requirement_ids=("req-000000000000000000000000",),
            reason="Invented alternative.",
        )

    with pytest.raises(ValueError):
        validate_batch_result(
            BatchSemanticMappingResult(decisions=tuple(decisions)), evidence, targets
        )


def test_mapped_decision_requires_a_target() -> None:
    with pytest.raises(ValidationError):
        EvidenceMappingDecision(
            evidence_id="ev-1",
            status="mapped",
            reason="Missing target.",
        )


def test_cycle_output_schema_rejects_mistyped_requirement_id() -> None:
    evidence = lean_evidence(package())
    targets = lean_targets(inventory())
    manufacturer_name = next(
        item for item in targets if item.path == ("Nameplate", "ManufacturerName")
    )
    assert manufacturer_name.id == "req-492c273471a954d2dd6a376b"

    output_type = constrained_batch_output_type(evidence, targets)
    with pytest.raises(ValidationError):
        output_type.model_validate(
            {
                "decisions": [
                    {
                        "evidenceId": evidence[0].id,
                        "status": "mapped",
                        "requirementId": "req-492c273471a9542dd6a376b",
                        "reason": "Mistyped canonical target ID.",
                    }
                ]
            }
        )


def test_cycle_output_schema_requires_target_for_mapped_status() -> None:
    evidence = lean_evidence(package())
    output_type = constrained_batch_output_type(evidence, lean_targets(inventory()))

    with pytest.raises(ValidationError):
        output_type.model_validate(
            {
                "decisions": [
                    {
                        "evidenceId": evidence[0].id,
                        "status": "mapped",
                        "reason": "No matching target exists.",
                    }
                ]
            }
        )


def test_batch_result_cannot_contradict_deterministic_authority() -> None:
    evidence = lean_evidence(package())
    targets = lean_targets(inventory())
    hint = DeterministicMappingHint(
        evidence_id="ev-1",
        requirement_id=targets[0].id,
    )
    with pytest.raises(ValueError, match="contradicted"):
        validate_batch_result(unmapped_result(), evidence, targets, (hint,))


def test_real_pydanticai_semantic_mapper_makes_one_structured_request() -> None:
    source = package()
    target_inventory = inventory()
    decisions = [item.model_dump(mode="json") for item in unmapped_result().decisions]
    mapper = PydanticBatchSemanticMapper(TestModel(custom_output_args={"decisions": decisions}))

    result = asyncio.run(mapper.map(source, target_inventory, MappingResult()))

    assert result.metrics.model_requests == 1
    assert len(result.result.decisions) == len(source.evidence)

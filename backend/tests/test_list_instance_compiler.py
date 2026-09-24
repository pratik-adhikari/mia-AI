"""Compile repeated IDTA list instances from explicit mapping bindings."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from aas_core3 import jsonization, verification

from mia_dpp.aas import AasCompiler
from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.evidence import (
    EvidenceRecord,
    EvidenceStatus,
    ProductKnowledgePackage,
    SourceLocation,
)
from mia_dpp.domain.mappings import (
    FieldMapping,
    ListInstanceBinding,
    MappingStatus,
    MappingTarget,
)
from mia_dpp.semantic.open_property import technical_property_area_binding
from mia_dpp.tools.mapping.confidence import (
    MatchQuality,
    ValueFormatQuality,
    assess_mapping,
)
from mia_dpp.tools.mapping.targets import (
    TECHNICAL_DATA_ARBITRARY_PROPERTY_PATH,
    mapping_target,
)


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
        source_uri="urn:test:list-instances",
        source_content_sha256="0" * 64,
        source_location=SourceLocation(excerpt=f"{label}: {value}"),
        extraction_method="test",
        extractor_name="test",
        extractor_version="1",
        status=EvidenceStatus.VERIFIED,
        acquired_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_compiler_materializes_distinct_technical_property_area_instances() -> None:
    repository = OfficialTemplateRepository()
    template = repository.load("technical_data")
    motor_a = ("Technical Specifications", "Motor A")
    motor_b = ("Technical Specifications", "Motor B")
    evidence = (
        _record("ev-a-power", "Rated power", "500", motor_a),
        _record("ev-a-voltage", "Voltage", "48", motor_a),
        _record("ev-b-power", "Rated power", "700", motor_b),
        _record("ev-b-voltage", "Voltage", "48", motor_b),
    )
    package = ProductKnowledgePackage(
        product_id="robot-1",
        product_name="Robot",
        evidence=evidence,
    )
    assessment = assess_mapping(
        source_label=MatchQuality.STRONG,
        value_format=ValueFormatQuality.VALID,
        semantic_match=MatchQuality.STRONG,
        destination_candidates=1,
    )
    concept_by_label = {
        "Rated power": ("RatedPower", "0173-1#02-POWER#001"),
        "Voltage": ("Voltage", "0173-1#02-VOLTAGE#001"),
    }

    mappings: list[FieldMapping] = []
    for record in evidence:
        id_short, semantic_id = concept_by_label[record.source_label or ""]
        binding = technical_property_area_binding(record.context_path)
        target = mapping_target(
            template,
            TECHNICAL_DATA_ARBITRARY_PROPERTY_PATH,
            id_short=id_short,
            semantic_id=semantic_id,
            list_instance_bindings=(binding,),
        )
        mappings.append(
            FieldMapping(
                id=f"mapping-{record.id}",
                evidence_id=record.id,
                source_field=record.source_label or record.predicate,
                source_value=str(record.value),
                target=target,
                assessment=assessment,
                reasoning="Fixture verified open Technical Property.",
                status=MappingStatus.AUTO,
            )
        )

    assert mappings[0].target.instance_path == mappings[2].target.instance_path
    assert (
        mappings[0].target.projection_identity
        != mappings[2].target.projection_identity
    )

    artifact = AasCompiler(repository).compile(package, mappings, template)
    environment = jsonization.environment_from_jsonable(artifact.environment)
    assert list(verification.verify(environment)) == []

    technical_areas = next(
        item
        for item in artifact.submodel["submodelElements"]
        if item.get("idShort") == "TechnicalPropertyAreas"
    )
    area_instances = technical_areas["value"]
    assert len(area_instances) == 2

    property_sets = []
    for area in area_instances:
        assert "idShort" not in area
        values = {
            item["idShort"]: item.get("value")
            for item in area["value"]
        }
        property_sets.append(values)

    assert {
        tuple(sorted(values.items()))
        for values in property_sets
    } == {
        (("RatedPower", "500"), ("Voltage", "48")),
        (("RatedPower", "700"), ("Voltage", "48")),
    }


def test_same_list_binding_still_rejects_duplicate_projection_identity() -> None:
    repository = OfficialTemplateRepository()
    template = repository.load("technical_data")
    context = ("Technical Specifications", "Motor A")
    binding = technical_property_area_binding(context)
    evidence = (
        _record("ev-1", "Rated power", "500", context),
        _record("ev-2", "Rated power", "500", context),
    )
    package = ProductKnowledgePackage(
        product_id="robot-1",
        product_name="Robot",
        evidence=evidence,
    )
    assessment = assess_mapping(
        source_label=MatchQuality.STRONG,
        value_format=ValueFormatQuality.VALID,
        semantic_match=MatchQuality.STRONG,
        destination_candidates=1,
    )
    target = mapping_target(
        template,
        TECHNICAL_DATA_ARBITRARY_PROPERTY_PATH,
        id_short="RatedPower",
        semantic_id="0173-1#02-POWER#001",
        list_instance_bindings=(binding,),
    )
    mappings = tuple(
        FieldMapping(
            id=f"mapping-{record.id}",
            evidence_id=record.id,
            source_field="Rated power",
            source_value=str(record.value),
            target=target,
            assessment=assessment,
            reasoning="Duplicate fixture.",
            status=MappingStatus.AUTO,
        )
        for record in evidence
    )

    from mia_dpp.errors import MappingError

    with pytest.raises(MappingError, match="projection identities must be unique"):
        AasCompiler(repository).compile(package, mappings, template)


def test_unbound_legacy_wildcard_target_remains_backward_compatible() -> None:
    repository = OfficialTemplateRepository()
    template = repository.load("technical_data")
    record = _record(
        "ev-legacy",
        "Rated power",
        "500",
        ("Technical Specifications",),
    )
    package = ProductKnowledgePackage(
        product_id="robot-1",
        product_name="Robot",
        evidence=(record,),
    )
    assessment = assess_mapping(
        source_label=MatchQuality.STRONG,
        value_format=ValueFormatQuality.VALID,
        semantic_match=MatchQuality.STRONG,
        destination_candidates=1,
    )
    target = mapping_target(
        template,
        TECHNICAL_DATA_ARBITRARY_PROPERTY_PATH,
        id_short="RatedPower",
        semantic_id="0173-1#02-POWER#001",
    )
    mapping = FieldMapping(
        id="mapping-legacy",
        evidence_id=record.id,
        source_field="Rated power",
        source_value="500",
        target=target,
        assessment=assessment,
        reasoning="Legacy wildcard mapping without explicit list binding.",
        status=MappingStatus.AUTO,
    )

    artifact = AasCompiler(repository).compile(package, (mapping,), template)
    technical_areas = next(
        item
        for item in artifact.submodel["submodelElements"]
        if item.get("idShort") == "TechnicalPropertyAreas"
    )

    assert target.list_instance_bindings == ()
    assert len(technical_areas["value"]) == 1
    assert technical_areas["value"][0]["value"][0]["idShort"] == "RatedPower"


def test_mapping_target_rejects_binding_outside_target_template_path() -> None:
    repository = OfficialTemplateRepository()
    template = repository.load("technical_data")
    invalid = ListInstanceBinding(
        template_path=("TechnicalData", "Other", "[]"),
        instance_key="list-instance-" + "a" * 24,
        source_context_path=("Motor A",),
        label="Motor A",
    )

    with pytest.raises(ValueError, match=r"must identify a \[\] in template_path"):
        mapping_target(
            template,
            TECHNICAL_DATA_ARBITRARY_PROPERTY_PATH,
            id_short="RatedPower",
            semantic_id="0173-1#02-POWER#001",
            list_instance_bindings=(invalid,),
        )


def test_legacy_mapping_target_json_loads_without_list_bindings() -> None:
    repository = OfficialTemplateRepository()
    template = repository.load("technical_data")
    target = mapping_target(
        template,
        TECHNICAL_DATA_ARBITRARY_PROPERTY_PATH,
        id_short="RatedPower",
        semantic_id="0173-1#02-POWER#001",
    )
    payload = target.model_dump(mode="json", exclude={"list_instance_bindings"})

    restored = MappingTarget.model_validate(payload)

    assert restored.list_instance_bindings == ()
    assert restored.instance_path == target.instance_path
    assert restored.semantic_id == target.semantic_id

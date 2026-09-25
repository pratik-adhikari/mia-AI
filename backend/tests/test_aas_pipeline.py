"""End-to-end tests for the deterministic official-template pipeline."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from aas_core3 import jsonization, verification

from mia_dpp.aas.build import build_dpp
from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.mappings import FieldMapping, MappingStatus, MappingTarget
from mia_dpp.errors import MappingError
from mia_dpp.semantic.open_property import technical_property_area_binding
from mia_dpp.tools.mapping.confidence import (
    MatchQuality,
    ValueFormatQuality,
    assess_mapping,
)
from mia_dpp.tools.mapping.targets import (
    TECHNICAL_DATA_ARBITRARY_PROPERTY_PATH,
    external_reference,
    mapping_target,
)
from mia_dpp.tools.mapping.text_mapping import propose_text_mappings

PRODUCT = (
    "AFRISO gauge, model RF100-16, serial number 2024-8871, built 2024, "
    "IP65, 0-16 bar, material number 63820."
)


def accepted_mappings(text: str = PRODUCT) -> tuple[str, list[FieldMapping]]:
    proposal = propose_text_mappings(text, OfficialTemplateRepository())
    mappings = [
        mapping.model_copy(update={"id": f"mapping-{index}", "status": MappingStatus.APPROVED})
        for index, mapping in enumerate(proposal.mappings)
    ]
    return proposal.product_name, mappings


def test_text_mapping_uses_official_and_wildcard_template_paths() -> None:
    proposal = propose_text_mappings(PRODUCT, OfficialTemplateRepository())

    targets = {item.target.id_short: item.target for item in proposal.mappings}
    assert proposal.product_name == "RF100-16"
    assert targets["ManufacturerName"].template_release == "3.0.1"
    assert targets["ManufacturerName"].semantic_id.primary_value == ("0112/2///61987#ABA565#009")
    assert targets["OrderCodeOfManufacturer"].template_path == (
        "Nameplate",
        "OrderCodeOfManufacturer",
    )
    assert targets["DegreeOfProtection"].template_path == (
        "Nameplate",
        "AssetSpecificProperties",
        "ArbitraryProperty",
    )
    assert targets["DegreeOfProtection"].instance_path[-1] == "DegreeOfProtection"
    assert all(item.evidence_id.startswith("ev-") for item in proposal.mappings)


def test_pipeline_builds_a_repeatable_aas_core_verified_environment() -> None:
    product_name, mappings = accepted_mappings()
    repository = OfficialTemplateRepository()
    first = build_dpp(
        product_name,
        mappings,
        repository=repository,
        now=datetime(2026, 1, 2, tzinfo=UTC),
    )
    second = build_dpp(
        product_name,
        list(reversed(mappings)),
        repository=repository,
        now=datetime(2027, 2, 3, tzinfo=UTC),
    )

    environment = jsonization.environment_from_jsonable(first.environment)
    assert list(verification.verify(environment)) == []
    assert first.artifact_sha256 == second.artifact_sha256
    assert first.environment == second.environment
    assert first.generated_at != second.generated_at
    assert first.deployable
    assert first.validation_report.valid
    assert first.validation_report.findings[0].code == "IDTA-EXTERNAL-DROPIN"
    elements = {item["idShort"]: item for item in first.submodel["submodelElements"]}
    assert elements["ManufacturerName"]["modelType"] == "MultiLanguageProperty"
    assert elements["ManufacturerName"]["value"] == [{"language": "en", "text": "AFRISO"}]
    assert elements["URIOfTheProduct"]["value"].startswith("urn:mia:asset:")


def test_missing_required_template_elements_are_reported_and_block_deployment() -> None:
    product_name, mappings = accepted_mappings(
        "SCHUNK clamping module, order code JGZ-100-1, 2022."
    )

    package = build_dpp(
        product_name,
        mappings,
        now=datetime(2026, 1, 2, tzinfo=UTC),
    )

    assert not package.deployable
    assert not package.validation_report.valid
    assert package.gap_report.blocks_deployment
    assert [gap.template_path for gap in package.gap_report.gaps] == [
        ("Nameplate", "ManufacturerProductDesignation")
    ]


def test_client_cannot_replace_an_official_fixed_semantic_id() -> None:
    product_name, mappings = accepted_mappings()
    original = mappings[0]
    forged_reference = external_reference("https://attacker.example/not-idta")
    target_data = original.target.model_dump(by_alias=False)
    target_data["semantic_id"] = forged_reference
    forged_target = MappingTarget(**target_data)
    mapping_data = original.model_dump(exclude={"target"})
    forged = FieldMapping(
        **mapping_data,
        target=forged_target,
    )

    with pytest.raises(MappingError, match="semantic ID differs"):
        build_dpp(product_name, [forged])


def test_rejected_and_pending_mappings_do_not_enter_the_artifact() -> None:
    product_name, mappings = accepted_mappings()
    mappings[0] = mappings[0].model_copy(update={"status": MappingStatus.REJECTED})
    mappings[1] = mappings[1].model_copy(update={"status": MappingStatus.REVIEW})

    package = build_dpp(product_name, mappings)

    rendered = str(package.environment)
    assert mappings[0].source_value not in rendered
    assert mappings[1].source_value not in rendered


def test_dpp_assembles_nameplate_and_technical_data_under_one_shell() -> None:
    product_name, nameplate = accepted_mappings()
    repository = OfficialTemplateRepository()
    technical = repository.load("technical_data")
    assessment = assess_mapping(
        source_label=MatchQuality.EXACT,
        value_format=ValueFormatQuality.VALID,
        semantic_match=MatchQuality.EXACT,
        destination_candidates=1,
    )
    general_values = {
        "ManufacturerName": "AFRISO",
        "ManufacturerProductDesignation": "RF100-16",
        "ManufacturerArticleNumber": "63820",
        "ManufacturerOrderCode": "PG16-ORDER",
    }
    technical_mappings = [
        FieldMapping(
            id=f"mapping-tech-{name}",
            evidence_id=f"ev-tech-{name}",
            source_field=name,
            source_value=value,
            target=mapping_target(
                technical,
                ("TechnicalData", "GeneralInformation", name),
            ),
            assessment=assessment,
            reasoning="Technical Data fixture.",
            status=MappingStatus.APPROVED,
        )
        for name, value in general_values.items()
    ]
    technical_mappings.append(
        FieldMapping(
            id="mapping-tech-rated-power",
            evidence_id="ev-tech-rated-power",
            source_field="Rated power",
            source_value="500",
            target=mapping_target(
                technical,
                TECHNICAL_DATA_ARBITRARY_PROPERTY_PATH,
                id_short="RatedPower",
                semantic_id="0173-1#02-POWER#001",
                list_instance_bindings=(
                    technical_property_area_binding(("Technical Specifications", "Motor A")),
                ),
            ),
            assessment=assessment,
            reasoning="Verified ECLASS Technical Property fixture.",
            status=MappingStatus.APPROVED,
        )
    )

    package = build_dpp(
        product_name,
        [*nameplate, *technical_mappings],
        repository=repository,
        now=datetime(2026, 1, 2, tzinfo=UTC),
    )

    environment = jsonization.environment_from_jsonable(package.environment)
    assert list(verification.verify(environment)) == []
    assert len(package.environment["assetAdministrationShells"]) == 1
    assert len(package.environment["submodels"]) == 2
    assert {item["idShort"] for item in package.environment["submodels"]} == {
        "Nameplate",
        "TechnicalData",
    }
    shell = package.environment["assetAdministrationShells"][0]
    assert len(shell["submodels"]) == 2
    assert len(package.submodels) == 2
    assert {item.key for item in package.templates} == {
        "digital_nameplate",
        "technical_data",
    }
    assert len(package.validation_reports) == 2

    technical_submodel = next(
        item for item in package.environment["submodels"] if item["idShort"] == "TechnicalData"
    )
    technical_areas = next(
        item
        for item in technical_submodel["submodelElements"]
        if item.get("idShort") == "TechnicalPropertyAreas"
    )
    rendered = str(technical_areas)
    assert "RatedPower" in rendered
    assert "0173-1#02-POWER#001" in rendered

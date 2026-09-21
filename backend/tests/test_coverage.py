from __future__ import annotations

from datetime import UTC, datetime

from mia_dpp.aas.requirements import build_template_index
from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.evidence import (
    EvidenceRecord,
    EvidenceStatus,
    ProductKnowledgePackage,
    SourceLocation,
)
from mia_dpp.domain.mappings import (
    CoverageReport,
    CoverageStatus,
    EvidenceOutcome,
    EvidenceOutcomeStatus,
    MappingOrigin,
    MappingResult,
    RequirementCoverage,
)
from mia_dpp.domain.targets import Requirement
from mia_dpp.tools.mapping.coverage import coverage


def evidence(identifier: str, label: str, value: str, unit: str | None = None) -> EvidenceRecord:
    return EvidenceRecord(
        id=identifier,
        predicate="source." + ".".join(label.casefold().split()),
        source_label=label,
        value=value,
        unit=unit,
        source_uri="https://manufacturer.example/product",
        source_content_sha256="0" * 64,
        source_location=SourceLocation(excerpt=f"{label}: {value}"),
        extraction_method="test",
        extractor_name="test",
        extractor_version="1",
        status=EvidenceStatus.OBSERVED,
        acquired_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def analyze(
    *records: EvidenceRecord,
    templates: tuple[str, ...] = ("digital_nameplate",),
) -> CoverageReport:
    repository = OfficialTemplateRepository()
    inventory = build_template_index([repository.load(key) for key in templates])
    package = ProductKnowledgePackage(
        product_id="product-test",
        product_name="Test product",
        evidence=records,
    )
    return coverage(package, inventory)


def coverage_for(
    report: CoverageReport,
    template: str,
    id_short: str,
) -> tuple[Requirement, RequirementCoverage]:
    inventory = report.inventory
    requirement = next(
        item
        for item in inventory.requirements
        if item.template_key == template and item.id_short == id_short
    )
    return requirement, next(
        item for item in report.coverage if item.requirement_id == requirement.id
    )


def test_known_alias_satisfies_manufacturer_in_each_selected_template() -> None:
    manufacturer = evidence("ev-manufacturer", "Manufacturer", "Example GmbH")
    report = analyze(
        manufacturer,
        templates=("digital_nameplate", "technical_data"),
    )

    for template in ("digital_nameplate", "technical_data"):
        _, item = coverage_for(report, template, "ManufacturerName")
        assert item.status is CoverageStatus.SATISFIED
        assert item.supporting_evidence_ids == (manufacturer.id,)
        assert item.match_method == "known_alias"
    assert report.statistics.evidence_used == 1
    assert report.unmatched_evidence_ids == ()


def test_specific_label_overlap_is_only_a_candidate() -> None:
    product_family = evidence("ev-family", "Product family", "Pressure sensors")
    report = analyze(product_family)

    _, item = coverage_for(report, "digital_nameplate", "ManufacturerProductFamily")
    assert item.status is CoverageStatus.CANDIDATE
    assert item.candidate_evidence_ids == (product_family.id,)
    assert item.match_method == "label_candidate"


def test_required_and_optional_missing_are_counted_separately() -> None:
    report = analyze(evidence("ev-protocol", "Protocol", "PROFINET"))

    required, required_coverage = coverage_for(
        report,
        "digital_nameplate",
        "ManufacturerName",
    )
    optional, optional_coverage = coverage_for(
        report,
        "digital_nameplate",
        "SerialNumber",
    )
    assert required.required is True
    assert required_coverage.status is CoverageStatus.MISSING
    assert optional.required is False
    assert optional_coverage.status is CoverageStatus.MISSING
    assert report.statistics.required_missing > 0
    assert report.statistics.optional_missing > 0


def test_vendor_specific_evidence_remains_unmatched_and_in_the_package() -> None:
    records = (
        evidence("ev-protocol", "Protocol", "PROFINET"),
        evidence("ev-material", "Material", "Aluminium"),
        evidence("ev-vendor", "Some vendor-specific value", "XYZ"),
    )
    report = analyze(*records, templates=("digital_nameplate", "technical_data"))

    assert set(report.analyzed_evidence_ids) == {item.id for item in records}
    assert set(report.unmatched_evidence_ids) == {item.id for item in records}
    assert report.statistics.evidence_used == 0
    assert report.statistics.unmatched_evidence == 3


def test_one_label_with_multiple_plausible_targets_is_ambiguous() -> None:
    article = evidence("ev-article", "Article number", "63820")
    report = analyze(
        article,
        templates=("digital_nameplate", "technical_data"),
    )

    _, nameplate = coverage_for(
        report,
        "digital_nameplate",
        "ProductArticleNumberOfManufacturer",
    )
    _, technical = coverage_for(
        report,
        "technical_data",
        "ManufacturerArticleNumber",
    )
    assert nameplate.status is CoverageStatus.AMBIGUOUS
    assert technical.status is CoverageStatus.AMBIGUOUS
    assert nameplate.candidate_evidence_ids == (article.id,)
    assert article.id not in report.unmatched_evidence_ids


def test_conflicting_exact_values_are_ambiguous_instead_of_selected() -> None:
    first = evidence("ev-maker-1", "ManufacturerName", "Example GmbH")
    second = evidence("ev-maker-2", "ManufacturerName", "Other GmbH")
    report = analyze(first, second)

    _, item = coverage_for(report, "digital_nameplate", "ManufacturerName")
    assert item.status is CoverageStatus.AMBIGUOUS
    assert item.match_method == "conflicting_deterministic_evidence"
    assert set(item.candidate_evidence_ids) == {first.id, second.id}


def test_reviewed_unmapped_evidence_is_not_remapped_by_label_heuristics() -> None:
    repository = OfficialTemplateRepository()
    inventory = build_template_index([repository.load("digital_nameplate")])
    manufacturer = evidence("ev-manufacturer", "Manufacturer", "Example GmbH")
    package = ProductKnowledgePackage(
        product_id="product-reviewed",
        product_name="Reviewed product",
        evidence=(manufacturer,),
    )
    mapping = MappingResult(
        unmatched_evidence_ids=(manufacturer.id,),
        outcomes=(
            EvidenceOutcome(
                evidence_id=manufacturer.id,
                status=EvidenceOutcomeStatus.UNMAPPED,
                reason="The human kept this evidence unmapped.",
                mapping_origin=MappingOrigin.HUMAN,
            ),
        ),
    )

    report = coverage(package, inventory, mapping_result=mapping)
    _, item = coverage_for(report, "digital_nameplate", "ManufacturerName")

    assert item.status is CoverageStatus.MISSING
    assert report.unmatched_evidence_ids == (manufacturer.id,)

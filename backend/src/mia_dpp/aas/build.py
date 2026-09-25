"""Application service composing evidence, reviewed mappings, compilation and validation."""

from __future__ import annotations

import copy
import hashlib
from datetime import UTC, datetime
from typing import Any, cast

from aas_core3 import jsonization, verification

from mia_dpp.aas import AasCompiler, AasValidator, gap_report_from_validation
from mia_dpp.aas.models import (
    AasArtifact,
    DppPackage,
    GapReport,
    Severity,
    ValidationCategory,
    ValidationFinding,
    ValidationReport,
)
from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.canonical import sha256_json
from mia_dpp.domain.evidence import (
    EvidenceRecord,
    EvidenceStatus,
    ProductKnowledgePackage,
    SourceLocation,
)
from mia_dpp.domain.mappings import FieldMapping, MappingStatus
from mia_dpp.domain.targets import TemplateRelease
from mia_dpp.errors import MappingError

DPP_TEMPLATE_KEY = "digital_nameplate"


def build_dpp(
    product_name: str,
    mappings: list[FieldMapping],
    *,
    repository: OfficialTemplateRepository | None = None,
    evidence: tuple[EvidenceRecord, ...] = (),
    now: datetime | None = None,
) -> DppPackage:
    """Build and verify one AAS shell containing every accepted mapped submodel."""

    templates = repository or OfficialTemplateRepository()
    accepted = [
        mapping
        for mapping in mappings
        if mapping.status in {MappingStatus.AUTO, MappingStatus.APPROVED}
    ]
    if not accepted:
        raise MappingError("at least one accepted mapping is required")

    by_template: dict[str, list[FieldMapping]] = {}
    for mapping in accepted:
        template = templates.load(mapping.target.template_key)
        if mapping.target.template_release != template.release.release:
            raise MappingError("mapping target belongs to another template release")
        by_template.setdefault(template.release.key, []).append(mapping)

    if DPP_TEMPLATE_KEY not in by_template:
        raise MappingError("a DPP requires at least one accepted Digital Nameplate mapping")

    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)

    selected_evidence = _select_evidence(
        accepted,
        evidence,
        product_name=product_name,
        acquired_at=moment,
    )
    package = ProductKnowledgePackage(
        product_id=f"product-{hashlib.sha256(product_name.encode()).hexdigest()[:24]}",
        product_name=product_name,
        evidence=selected_evidence,
    )

    ordered_keys = (
        DPP_TEMPLATE_KEY,
        *sorted(key for key in by_template if key != DPP_TEMPLATE_KEY),
    )
    global_seed = {
        "productName": product_name,
        "templates": [templates.load(key).release.model_dump(mode="json") for key in ordered_keys],
        "mappings": [
            {
                "evidenceId": mapping.evidence_id,
                "sourceValue": mapping.source_value,
                "templateKey": mapping.target.template_key,
                "templatePath": mapping.target.template_path,
                "instancePath": mapping.target.instance_path,
                "semanticId": mapping.target.semantic_id.primary_value,
                "listInstances": [
                    {
                        "path": binding.template_path,
                        "key": binding.instance_key,
                    }
                    for binding in mapping.target.list_instance_bindings
                ],
            }
            for mapping in sorted(
                accepted,
                key=lambda item: (
                    item.target.template_key,
                    item.target.projection_identity,
                    item.evidence_id,
                ),
            )
        ],
    }
    suffix = sha256_json(global_seed)[:32]
    aas_id = f"urn:mia:aas:{suffix}"
    asset_id = f"urn:mia:asset:{suffix}"

    compiler = AasCompiler(templates)
    validator = AasValidator()
    artifacts: list[AasArtifact] = []
    validation_reports: list[ValidationReport] = []
    gap_reports: list[GapReport] = []
    template_releases: list[TemplateRelease] = []

    for key in ordered_keys:
        template = templates.load(key)
        template_releases.append(template.release)
        template_mappings = sorted(
            by_template[key],
            key=lambda item: (item.target.projection_identity, item.evidence_id),
        )
        artifact = compiler.compile(
            package,
            template_mappings,
            template,
            aas_id=aas_id,
            asset_id=asset_id,
            submodel_id=f"urn:mia:submodel:{key}:{suffix}",
        )
        report = validator.validate(artifact, template, template_mappings)
        artifacts.append(artifact)
        validation_reports.append(report)
        gap_reports.append(gap_report_from_validation(report))

    primary = artifacts[0]
    shell = copy.deepcopy(cast(dict[str, Any], primary.environment["assetAdministrationShells"][0]))
    submodels = tuple(copy.deepcopy(artifact.submodel) for artifact in artifacts)
    shell["submodels"] = [
        {
            "type": "ModelReference",
            "keys": [{"type": "Submodel", "value": str(submodel["id"])}],
        }
        for submodel in submodels
    ]
    environment: dict[str, Any] = {
        "assetAdministrationShells": [shell],
        "submodels": list(submodels),
    }
    artifact_sha = sha256_json(environment)

    aggregate_findings = [finding for report in validation_reports for finding in report.findings]
    try:
        parsed = jsonization.environment_from_jsonable(environment)
        aggregate_findings.extend(
            ValidationFinding(
                category=ValidationCategory.METAMODEL,
                code="AAS-METAMODEL-AGGREGATE",
                message=str(error.cause),
                severity=Severity.ERROR,
                instance_path=(str(error.path),),
            )
            for error in verification.verify(parsed)
        )
    except (TypeError, ValueError) as error:
        aggregate_findings.append(
            ValidationFinding(
                category=ValidationCategory.METAMODEL,
                code="AAS-DESERIALIZATION-AGGREGATE",
                message=str(error),
                severity=Severity.ERROR,
            )
        )

    aggregate_template_key = (
        template_releases[0].key if len(template_releases) == 1 else "multi_submodel"
    )
    aggregate_template_release = (
        template_releases[0].release
        if len(template_releases) == 1
        else "+".join(release.release for release in template_releases)
    )
    aggregate_validation = ValidationReport(
        valid=not any(finding.severity is Severity.ERROR for finding in aggregate_findings),
        template_key=aggregate_template_key,
        template_release=aggregate_template_release,
        artifact_sha256=artifact_sha,
        validator_versions={
            **{
                name: value
                for report in validation_reports
                for name, value in report.validator_versions.items()
            },
            "mia-multi-submodel-assembler": "1",
        },
        findings=tuple(aggregate_findings),
    )
    aggregate_gaps = tuple(gap for report in gap_reports for gap in report.gaps)
    aggregate_gap_report = GapReport(
        template_key=aggregate_template_key,
        gaps=aggregate_gaps,
        blocks_deployment=any(gap.severity is Severity.ERROR for gap in aggregate_gaps),
    )
    deployable = aggregate_validation.valid and not aggregate_gap_report.blocks_deployment

    return DppPackage(
        product_name=product_name,
        generated_at=moment.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        passport_id=aas_id,
        submodel=primary.submodel,
        environment=environment,
        artifact_sha256=artifact_sha,
        template=template_releases[0],
        gap_report=aggregate_gap_report,
        validation_report=aggregate_validation,
        deployable=deployable,
        evidence=selected_evidence,
        submodels=submodels,
        templates=tuple(template_releases),
        gap_reports=tuple(gap_reports),
        validation_reports=tuple(validation_reports),
    )


def _select_evidence(
    mappings: list[FieldMapping],
    supplied: tuple[EvidenceRecord, ...],
    *,
    product_name: str,
    acquired_at: datetime,
) -> tuple[EvidenceRecord, ...]:
    if not supplied:
        generated = tuple(_evidence(mapping, product_name, acquired_at) for mapping in mappings)
        return _deduplicate_evidence(generated)

    by_id = {item.id: item for item in supplied}
    if len(by_id) != len(supplied):
        raise MappingError("supplied evidence IDs must be unique")
    selected: list[EvidenceRecord] = []
    for mapping in mappings:
        record = by_id.get(mapping.evidence_id)
        if record is None:
            raise MappingError(
                f"mapping refers to missing supplied evidence {mapping.evidence_id!r}"
            )
        raw_value = str(record.value)
        displayed_value = (
            f"{raw_value} {record.unit}"
            if record.unit and not raw_value.endswith(record.unit)
            else raw_value
        )
        if mapping.source_value not in {raw_value, displayed_value}:
            raise MappingError(
                f"mapping value differs from supplied evidence {mapping.evidence_id!r}"
            )
        selected.append(record)
    return _deduplicate_evidence(tuple(selected))


def _deduplicate_evidence(
    evidence: tuple[EvidenceRecord, ...],
) -> tuple[EvidenceRecord, ...]:
    by_id: dict[str, EvidenceRecord] = {}
    for record in evidence:
        existing = by_id.get(record.id)
        if existing is not None and existing != record:
            raise MappingError(f"evidence ID {record.id!r} has conflicting records")
        by_id[record.id] = record
    return tuple(by_id.values())


def _evidence(
    mapping: FieldMapping,
    product_name: str,
    acquired_at: datetime,
) -> EvidenceRecord:
    source = f"{product_name}\0{mapping.source_field}\0{mapping.source_value}"
    content_hash = hashlib.sha256(source.encode()).hexdigest()
    normalized_field = "_".join(
        part
        for part in "".join(
            character.casefold() if character.isalnum() else " "
            for character in mapping.source_field
        ).split()
        if part
    )
    return EvidenceRecord(
        id=mapping.evidence_id,
        predicate=f"reviewed.{normalized_field or 'field'}",
        value=mapping.source_value,
        source_uri=f"urn:mia:review:{content_hash[:24]}",
        source_content_sha256=content_hash,
        source_location=SourceLocation(excerpt=mapping.source_value[:240]),
        extraction_method="reviewed_mapping",
        extractor_name="mia-workspace",
        extractor_version="2",
        status=(
            EvidenceStatus.VERIFIED
            if mapping.status is MappingStatus.APPROVED
            else EvidenceStatus.OBSERVED
        ),
        acquired_at=acquired_at,
    )

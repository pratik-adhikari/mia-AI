"""Deterministic AAS metamodel and official-template validation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from importlib.metadata import version
from typing import Any, cast

from aas_core3 import jsonization, verification

from mia_dpp.aas._structure import _children, _reference_value
from mia_dpp.aas.models import (
    AasArtifact,
    Gap,
    GapReport,
    Severity,
    ValidationCategory,
    ValidationFinding,
    ValidationReport,
)
from mia_dpp.canonical import sha256_json
from mia_dpp.domain.mappings import FieldMapping, MappingTarget
from mia_dpp.domain.targets import Cardinality, SubmodelTemplate, TemplateElement


class AasValidator:
    """Verify compiled artifacts against aas-core and official template rules.

    The deterministic DPP pipeline calls this immediately after compilation.
    Its report—not the agent or LLM—decides whether deployment is allowed.
    """

    def validate(
        self,
        artifact: AasArtifact,
        template: SubmodelTemplate,
        mappings: Sequence[FieldMapping],
    ) -> ValidationReport:
        """Return metamodel, template, and policy findings for one artifact."""

        findings: list[ValidationFinding] = []
        if sha256_json(artifact.environment) != artifact.sha256:
            findings.append(
                ValidationFinding(
                    category=ValidationCategory.POLICY,
                    code="MIA-DIGEST-001",
                    message="Artifact content differs from its recorded digest.",
                    severity=Severity.ERROR,
                )
            )
        try:
            environment = jsonization.environment_from_jsonable(artifact.environment)
            findings.extend(
                ValidationFinding(
                    category=ValidationCategory.METAMODEL,
                    code="AAS-METAMODEL",
                    message=str(error.cause),
                    severity=Severity.ERROR,
                    instance_path=(str(error.path),),
                )
                for error in verification.verify(environment)
            )
        except (TypeError, ValueError) as error:
            findings.append(
                ValidationFinding(
                    category=ValidationCategory.METAMODEL,
                    code="AAS-DESERIALIZATION",
                    message=str(error),
                    severity=Severity.ERROR,
                )
            )

        submodel = artifact.submodel
        if _reference_value(submodel.get("semanticId")) != template.semantic_id.primary_value:
            findings.append(
                ValidationFinding(
                    category=ValidationCategory.TEMPLATE,
                    code="IDTA-SUBMODEL-SEMANTIC-ID",
                    message="Submodel semantic ID differs from the selected official template.",
                    severity=Severity.ERROR,
                    expected=template.semantic_id.primary_value,
                    actual=_reference_value(submodel.get("semanticId")),
                )
            )
        actual_elements = submodel.get("submodelElements")
        actual_elements = actual_elements if isinstance(actual_elements, list) else []
        findings.extend(
            self._validate_children(
                template.elements,
                actual_elements,
                parent_present=True,
            )
        )
        for approved in mappings:
            findings.extend(self._validate_mapped_target(approved.target, actual_elements))
        if template.release.key == "digital_nameplate":
            findings.append(
                ValidationFinding(
                    category=ValidationCategory.TEMPLATE,
                    code="IDTA-EXTERNAL-DROPIN",
                    message=(
                        "AddressInformation is an external Contact Information drop-in; "
                        "the pinned Nameplate JSON does not contain its child definition."
                    ),
                    severity=Severity.WARNING,
                    instance_path=(template.id_short, "AddressInformation"),
                    template_path=(template.id_short, "AddressInformation"),
                )
            )
        return ValidationReport(
            valid=not any(item.severity is Severity.ERROR for item in findings),
            template_key=template.release.key,
            template_release=template.release.release,
            artifact_sha256=artifact.sha256,
            validator_versions={
                "aas-core3.0": version("aas-core3.0"),
                "mia-template-validator": "1",
            },
            findings=tuple(findings),
        )

    def _validate_children(
        self,
        expected: Sequence[TemplateElement],
        actual: Sequence[object],
        *,
        parent_present: bool,
    ) -> list[ValidationFinding]:
        if not parent_present:
            return []
        findings: list[ValidationFinding] = []
        actual_dicts = [item for item in actual if isinstance(item, Mapping)]
        for element in expected:
            if element.path[-1] == "[]":
                matches = actual_dicts
            elif element.wildcard:
                matches = [
                    item for item in actual_dicts if item.get("modelType") == element.model_type
                ]
            else:
                matches = [item for item in actual_dicts if item.get("idShort") == element.id_short]
            cardinality = element.cardinality or Cardinality.ZERO_TO_MANY
            if len(matches) < cardinality.minimum:
                findings.append(
                    ValidationFinding(
                        category=ValidationCategory.TEMPLATE,
                        code="IDTA-CARDINALITY-MIN",
                        message=f"Required template element {'/'.join(element.path)} is missing.",
                        severity=Severity.ERROR,
                        template_path=element.path,
                        expected=cardinality.value,
                        actual=str(len(matches)),
                    )
                )
                continue
            if cardinality.maximum is not None and len(matches) > cardinality.maximum:
                findings.append(
                    ValidationFinding(
                        category=ValidationCategory.TEMPLATE,
                        code="IDTA-CARDINALITY-MAX",
                        message=f"Template element {'/'.join(element.path)} occurs too often.",
                        severity=Severity.ERROR,
                        template_path=element.path,
                        expected=cardinality.value,
                        actual=str(len(matches)),
                    )
                )
            for match in matches:
                findings.extend(self._validate_element_shape(element, match))
                child_values = _children(cast(Mapping[str, Any], match))
                findings.extend(
                    self._validate_children(
                        element.children,
                        child_values,
                        parent_present=True,
                    )
                )
        return findings

    @staticmethod
    def _validate_element_shape(
        expected: TemplateElement, actual: Mapping[str, Any]
    ) -> list[ValidationFinding]:
        findings: list[ValidationFinding] = []
        actual_type = actual.get("modelType")
        if actual_type != expected.model_type:
            findings.append(
                ValidationFinding(
                    category=ValidationCategory.TEMPLATE,
                    code="IDTA-MODEL-TYPE",
                    message="Instance model type differs from the template.",
                    severity=Severity.ERROR,
                    instance_path=expected.path,
                    template_path=expected.path,
                    expected=expected.model_type,
                    actual=str(actual_type),
                )
            )
        actual_value_type = actual.get("valueType")
        if expected.value_type is not None and actual_value_type != expected.value_type:
            findings.append(
                ValidationFinding(
                    category=ValidationCategory.TEMPLATE,
                    code="IDTA-VALUE-TYPE",
                    message="Instance value type differs from the template.",
                    severity=Severity.ERROR,
                    instance_path=expected.path,
                    template_path=expected.path,
                    expected=expected.value_type,
                    actual=str(actual_value_type),
                )
            )
        if not expected.wildcard and expected.semantic_id is not None:
            actual_semantic_id = _reference_value(actual.get("semanticId"))
            expected_values = {key.value for key in expected.semantic_id.keys} | {
                key.value
                for reference in expected.supplemental_semantic_ids
                for key in reference.keys
            }
            if actual_semantic_id not in expected_values:
                findings.append(
                    ValidationFinding(
                        category=ValidationCategory.TEMPLATE,
                        code="IDTA-SEMANTIC-ID",
                        message="Instance semantic ID differs from the template.",
                        severity=Severity.ERROR,
                        instance_path=expected.path,
                        template_path=expected.path,
                        expected=" or ".join(sorted(expected_values)),
                        actual=actual_semantic_id,
                    )
                )
        return findings

    @staticmethod
    def _validate_mapped_target(
        target: MappingTarget, root: Sequence[object]
    ) -> list[ValidationFinding]:
        current: Sequence[Mapping[str, Any]] = [item for item in root if isinstance(item, Mapping)]
        for index, segment in enumerate(target.instance_path[1:]):
            if segment == "[]":
                matches = current
            else:
                matches = [item for item in current if item.get("idShort") == segment]
            if not matches:
                return [
                    ValidationFinding(
                        category=ValidationCategory.TEMPLATE,
                        code="MIA-MAPPED-TARGET-MISSING",
                        message="An approved mapping is absent from the compiled artifact.",
                        severity=Severity.ERROR,
                        instance_path=target.instance_path,
                        template_path=target.template_path,
                    )
                ]
            if index < len(target.instance_path[1:]) - 1:
                current = [child for match in matches for child in _children(match)]
        return []


def gap_report_from_validation(report: ValidationReport) -> GapReport:
    """Present missing required template elements separately from all validation detail."""

    gaps = tuple(
        Gap(
            template_path=finding.template_path,
            message=finding.message,
            severity=finding.severity,
        )
        for finding in report.findings
        if finding.code == "IDTA-CARDINALITY-MIN"
    )
    return GapReport(
        template_key=report.template_key,
        gaps=gaps,
        blocks_deployment=any(gap.severity is Severity.ERROR for gap in gaps),
    )

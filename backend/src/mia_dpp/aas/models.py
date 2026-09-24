"""Deterministic AAS artifact, validation, and deployment contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from mia_dpp.domain.base import WireModel
from mia_dpp.domain.evidence import EvidenceRecord
from mia_dpp.domain.targets import TemplateRelease


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ValidationCategory(StrEnum):
    METAMODEL = "metamodel"
    TEMPLATE = "template"
    POLICY = "policy"


class Gap(WireModel):
    template_path: tuple[str, ...]
    message: str
    severity: Severity


class GapReport(WireModel):
    template_key: str
    gaps: tuple[Gap, ...] = ()
    blocks_deployment: bool

    @model_validator(mode="after")
    def blocking_flag_matches_gaps(self) -> GapReport:
        expected = any(gap.severity is Severity.ERROR for gap in self.gaps)
        if self.blocks_deployment != expected:
            raise ValueError("blocksDeployment must match error-severity gaps")
        return self


class ValidationFinding(WireModel):
    category: ValidationCategory
    code: str
    message: str
    severity: Severity
    instance_path: tuple[str, ...] = ()
    template_path: tuple[str, ...] = ()
    expected: str | None = None
    actual: str | None = None


class ValidationReport(WireModel):
    valid: bool
    template_key: str
    template_release: str
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    validator_versions: dict[str, str]
    findings: tuple[ValidationFinding, ...] = ()

    @model_validator(mode="after")
    def valid_matches_findings(self) -> ValidationReport:
        expected = not any(item.severity is Severity.ERROR for item in self.findings)
        if self.valid != expected:
            raise ValueError("valid must be false exactly when an error finding exists")
        return self


class AasArtifact(WireModel):
    environment: dict[str, Any]
    submodel: dict[str, Any]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiler_name: str
    compiler_version: str


class DeploymentResult(WireModel):
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository_url: str = Field(min_length=1)
    shell_ids: tuple[str, ...]
    submodel_ids: tuple[str, ...]
    status: Literal["deployed"] = "deployed"


class DppPackage(WireModel):
    product_name: str
    generated_at: str
    submodel: dict[str, Any]
    environment: dict[str, Any]
    passport_id: str
    artifact_sha256: str
    template: TemplateRelease
    gap_report: GapReport
    validation_report: ValidationReport
    deployable: bool
    evidence: tuple[EvidenceRecord, ...] = ()
    submodels: tuple[dict[str, Any], ...] = ()
    templates: tuple[TemplateRelease, ...] = ()
    gap_reports: tuple[GapReport, ...] = ()
    validation_reports: tuple[ValidationReport, ...] = ()

"""Reusable target construction, deterministic mapping, and coverage operations."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from mia_dpp.aas.requirements import build_template_index
from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.mappings import CoverageStatus, MappingResult
from mia_dpp.domain.product_work import ProductWorkStage
from mia_dpp.domain.targets import RequirementKind, TemplateIndex
from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.runtime.run_context import RunContext
from mia_dpp.runtime.run_store import RunStore
from mia_dpp.services.product_snapshot import model_fingerprint, update_product_snapshot
from mia_dpp.storage.base import ArtifactStore
from mia_dpp.tools.mapping.coverage import coverage as calculate_coverage
from mia_dpp.tools.mapping.mapper import DeterministicWebsiteMapper


@dataclass(frozen=True, slots=True)
class TargetBuildResult:
    artifact_id: str
    target_fingerprint: str
    product_snapshot_version: int


@dataclass(frozen=True, slots=True)
class DeterministicMappingStageResult:
    artifact_id: str
    mapping_input_fingerprint: str
    product_snapshot_version: int


@dataclass(frozen=True, slots=True)
class CoverageStageResult:
    artifact_id: str
    unresolved_requirement_ids: tuple[str, ...]
    product_snapshot_version: int


class MappingExecutionService:
    """Prepare fixed targets, deterministic mappings, and coverage reports."""

    def __init__(
        self,
        *,
        catalogue: ProductCatalogue,
        artifacts: ArtifactStore,
        templates: OfficialTemplateRepository,
    ) -> None:
        self._catalogue = catalogue
        self._artifacts = artifacts
        self._templates = templates

    def _store(self, context: RunContext) -> RunStore:
        return RunStore(context, self._catalogue, self._artifacts)

    def build_targets(
        self,
        context: RunContext,
        *,
        evidence_artifact_id: str,
        template_keys: tuple[str, ...],
        expected_snapshot_version: int,
        source_generation: int,
    ) -> TargetBuildResult:
        work = self._store(context)
        templates = tuple(self._templates.load(key) for key in template_keys)
        index = build_template_index(templates)
        artifact_id = work.put_model(
            "mapping/targets.json",
            index,
            derived_from=(evidence_artifact_id,),
        )
        target_fingerprint = model_fingerprint(index)
        snapshot = update_product_snapshot(
            self._catalogue,
            context,
            ProductWorkStage.TARGETS,
            expected_version=expected_snapshot_version,
            source_generation=source_generation,
            template_keys=tuple(item.key for item in index.selected_templates),
            template_releases=tuple(item.release for item in index.selected_templates),
            targets_artifact_id=artifact_id,
            target_fingerprint=target_fingerprint,
        )
        return TargetBuildResult(
            artifact_id=artifact_id,
            target_fingerprint=target_fingerprint,
            product_snapshot_version=snapshot.version,
        )

    async def deterministic_map(
        self,
        context: RunContext,
        *,
        evidence_artifact_id: str,
        targets_artifact_id: str,
        evidence_fingerprint: str | None,
        source_fingerprint: str | None,
        target_fingerprint: str | None,
        expected_snapshot_version: int,
        source_generation: int,
    ) -> DeterministicMappingStageResult:
        work = self._store(context)
        package = work.load(evidence_artifact_id, ProductKnowledgePackage)
        index = work.load(targets_artifact_id, TemplateIndex)
        started = perf_counter()
        result = await DeterministicWebsiteMapper(
            self._templates,
            index,
        ).propose(package.evidence)
        duration_ms = round((perf_counter() - started) * 1000, 2)
        artifact_id = work.put_model(
            "mapping/deterministic.json",
            result,
            derived_from=(evidence_artifact_id, targets_artifact_id),
        )
        work.event(
            "mapping.deterministic.completed",
            "Completed deterministic mapping for the currently available evidence.",
            metadata={
                "durationMs": duration_ms,
                "evidenceCount": len(package.evidence),
                "mapped": len(result.mapped),
                "ambiguous": len(result.ambiguous),
                "unmatched": len(result.unmatched_evidence_ids),
            },
        )
        mapping_input_fingerprint = model_fingerprint(
            {
                "evidenceFingerprint": evidence_fingerprint or source_fingerprint,
                "targetFingerprint": target_fingerprint,
                "deterministic": result.model_dump(mode="json", by_alias=True),
            }
        )
        snapshot = update_product_snapshot(
            self._catalogue,
            context,
            ProductWorkStage.MAPPING,
            expected_version=expected_snapshot_version,
            source_generation=source_generation,
            deterministic_mapping_artifact_id=artifact_id,
            mapping_input_fingerprint=mapping_input_fingerprint,
        )
        return DeterministicMappingStageResult(
            artifact_id=artifact_id,
            mapping_input_fingerprint=mapping_input_fingerprint,
            product_snapshot_version=snapshot.version,
        )

    def coverage(
        self,
        context: RunContext,
        *,
        evidence_artifact_id: str,
        targets_artifact_id: str,
        mapping_artifact_id: str,
        expected_snapshot_version: int,
        source_generation: int,
    ) -> CoverageStageResult:
        work = self._store(context)
        package = work.load(evidence_artifact_id, ProductKnowledgePackage)
        index = work.load(targets_artifact_id, TemplateIndex)
        result = work.load(mapping_artifact_id, MappingResult)
        report = calculate_coverage(package, index, mapping_result=result)
        artifact_id = work.put_model(
            "mapping/coverage.json",
            report,
            derived_from=(mapping_artifact_id,),
        )
        requirements = {item.id: item for item in report.inventory.requirements}
        unresolved = tuple(
            item.requirement_id
            for item in report.coverage
            if requirements[item.requirement_id].required
            and requirements[item.requirement_id].kind is RequirementKind.VALUE
            and item.status is not CoverageStatus.SATISFIED
        )
        work.event(
            "coverage.completed",
            f"Coverage has {len(unresolved)} unresolved mandatory value requirements.",
            metadata={"requiredUnresolved": len(unresolved)},
        )
        snapshot = update_product_snapshot(
            self._catalogue,
            context,
            ProductWorkStage.COVERAGE,
            expected_version=expected_snapshot_version,
            source_generation=source_generation,
            coverage_artifact_id=artifact_id,
            unresolved_required_ids=unresolved,
        )
        return CoverageStageResult(
            artifact_id=artifact_id,
            unresolved_requirement_ids=unresolved,
            product_snapshot_version=snapshot.version,
        )

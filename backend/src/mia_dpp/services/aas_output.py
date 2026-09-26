"""Reusable deterministic AAS build, validation, and DPP release operations."""

from __future__ import annotations

from dataclasses import dataclass

from mia_dpp.aas.build import build_dpp
from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.base import utc_now
from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.mappings import MappingResult, MappingStatus
from mia_dpp.domain.product import DppReleaseStatus, RunStatus
from mia_dpp.domain.product_work import ProductWorkStage
from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.runtime.run_context import RunContext
from mia_dpp.runtime.run_store import RunStore
from mia_dpp.services.product_snapshot import model_fingerprint, update_product_snapshot
from mia_dpp.storage.base import ArtifactStore


@dataclass(frozen=True, slots=True)
class AasBuildResult:
    dpp_artifact_id: str
    aas_artifact_id: str
    validation_artifact_id: str
    deployable: bool
    build_input_fingerprint: str
    product_snapshot_version: int


@dataclass(frozen=True, slots=True)
class DppStoreResult:
    status: str
    release_status: DppReleaseStatus | None
    dummy_mapping_count: int
    validation_failed: bool = False
    dpp_version_id: str | None = None


class AasOutputService:
    """Build validated AAS artifacts and publish durable DPP versions."""

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

    def build(
        self,
        context: RunContext,
        *,
        evidence_artifact_id: str,
        mapping_artifact_id: str,
        coverage_artifact_id: str,
        expected_snapshot_version: int,
        source_generation: int,
    ) -> AasBuildResult:
        work = self._store(context)
        package_input = work.load(evidence_artifact_id, ProductKnowledgePackage)
        mapping = work.load(mapping_artifact_id, MappingResult)
        accepted = [
            item
            for item in mapping.mapped
            if item.status in {MappingStatus.AUTO, MappingStatus.APPROVED}
        ]
        package = build_dpp(
            package_input.product_name,
            accepted,
            repository=self._templates,
            evidence=package_input.evidence,
        )
        dpp_id = work.put_model(
            "dpp/package.json",
            package,
            derived_from=(mapping_artifact_id, coverage_artifact_id),
        )
        aas_id = work.put_json(
            "aas/environment.json",
            package.environment,
            derived_from=(dpp_id,),
        )
        validation_id = work.put_model(
            "aas/validation.json",
            package.validation_report,
            derived_from=(aas_id,),
        )
        work.event(
            "aas.validation_completed",
            "Built and deterministically validated the AAS artifact.",
            metadata={
                "deployable": package.deployable,
                "artifactSha256": package.artifact_sha256,
            },
        )
        build_input_fingerprint = model_fingerprint(
            {
                "evidence": package_input.model_dump(mode="json", by_alias=True),
                "mapping": mapping.model_dump(mode="json", by_alias=True),
            }
        )
        snapshot = update_product_snapshot(
            self._catalogue,
            context,
            ProductWorkStage.VALIDATION,
            expected_version=expected_snapshot_version,
            source_generation=source_generation,
            dpp_artifact_id=dpp_id,
            aas_artifact_id=aas_id,
            validation_artifact_id=validation_id,
            build_input_fingerprint=build_input_fingerprint,
            last_error=(
                None
                if package.deployable
                else "AAS validation did not produce a deployable artifact"
            ),
        )
        return AasBuildResult(
            dpp_artifact_id=dpp_id,
            aas_artifact_id=aas_id,
            validation_artifact_id=validation_id,
            deployable=package.deployable,
            build_input_fingerprint=build_input_fingerprint,
            product_snapshot_version=snapshot.version,
        )

    def store_result(
        self,
        context: RunContext,
        *,
        deployable: bool,
        mapping_artifact_id: str,
        dpp_artifact_id: str,
        aas_artifact_id: str | None,
        validation_artifact_id: str | None,
        source_fingerprint: str | None,
        required_unresolved: int,
        research_attempts: int,
        expected_snapshot_version: int,
        source_generation: int,
    ) -> DppStoreResult:
        work = self._store(context)
        mapping = work.load(mapping_artifact_id, MappingResult)
        dummy_mapping_ids = tuple(
            item.id
            for item in mapping.mapped
            if item.status in {MappingStatus.AUTO, MappingStatus.APPROVED}
            and item.human_value_kind == "dummy"
        )
        release_status = (
            DppReleaseStatus.PROVISIONAL if dummy_mapping_ids else DppReleaseStatus.VERIFIED
        )
        metrics: dict[str, int | float | str | bool | None] = {
            "requiredUnresolved": required_unresolved,
            "researchAttempts": research_attempts,
        }
        if not deployable:
            update_product_snapshot(
                self._catalogue,
                context,
                ProductWorkStage.FAILED,
                expected_version=expected_snapshot_version,
                source_generation=source_generation,
                last_error="AAS validation did not produce a deployable artifact",
            )
            work.event(
                "dpp.validation_failed",
                "Stored failed build/validation artifacts without publishing a DPP version.",
            )
            self._catalogue.finish_run(
                context.run_id,
                RunStatus.FAILED,
                metrics=metrics,
                error="AAS validation did not produce a deployable artifact",
            )
            return DppStoreResult(
                status="failed",
                release_status=None,
                dummy_mapping_count=len(dummy_mapping_ids),
                validation_failed=True,
            )

        version = self._catalogue.create_dpp_version(
            context.product_id,
            context.run_id,
            dpp_artifact_id=dpp_artifact_id,
            aas_artifact_id=aas_artifact_id,
            validation_artifact_id=validation_artifact_id,
            source_fingerprint=source_fingerprint,
            deployable=True,
            release_status=release_status,
            dummy_mapping_ids=dummy_mapping_ids,
        )
        product = self._catalogue.get_product(
            context.product_id,
            user_id=context.user_id,
        )
        if product is not None and release_status is DppReleaseStatus.VERIFIED:
            self._catalogue.update_product(
                product.model_copy(update={"last_verified_at": utc_now()})
            )
        update_product_snapshot(
            self._catalogue,
            context,
            ProductWorkStage.COMPLETED,
            expected_version=expected_snapshot_version,
            source_generation=source_generation,
            unresolved_required_ids=(),
            human_review_pending=False,
            last_error=None,
            release_status=release_status,
            dummy_mapping_ids=dummy_mapping_ids,
        )
        work.event(
            "dpp.version_created",
            f"Stored DPP version {version.version}.",
            metadata={"dppVersionId": version.id},
        )
        self._catalogue.finish_run(
            context.run_id,
            RunStatus.COMPLETED,
            metrics=metrics,
        )
        return DppStoreResult(
            status="completed",
            release_status=release_status,
            dummy_mapping_count=len(dummy_mapping_ids),
            dpp_version_id=version.id,
        )

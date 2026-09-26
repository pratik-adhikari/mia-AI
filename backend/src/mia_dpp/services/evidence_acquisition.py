"""Reusable evidence acquisition and persistence for one product run."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from mia_dpp.canonical import sha256_json
from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.evidence_merge import merge_packages
from mia_dpp.domain.product_work import ProductWorkStage
from mia_dpp.persistence.catalogue import ProductCatalogue, ProductIdentifierConflict
from mia_dpp.runtime.run_context import RunContext
from mia_dpp.runtime.run_store import RunStore
from mia_dpp.services.evidence_metadata import evidence_text, product_image_url
from mia_dpp.services.product_identifiers import discover_product_identifiers
from mia_dpp.services.product_snapshot import update_product_snapshot
from mia_dpp.storage.base import ArtifactStore
from mia_dpp.tools.web.tool import WebExtractionTool


@dataclass(frozen=True, slots=True)
class EvidenceAcquisitionRequest:
    context: RunContext
    product_url: str
    reuse_prior_work: bool
    prior_evidence_artifact_id: str | None
    refresh_requested: bool
    seeded_from_run_id: str | None
    reviewed_mapping_artifact_id: str | None
    target_submodels: tuple[str, ...]
    known_source_urls: tuple[str, ...]
    source_generation: int
    product_snapshot_version: int


@dataclass(frozen=True, slots=True)
class EvidenceAcquisitionResult:
    evidence_artifact_id: str
    product_name: str
    known_source_urls: tuple[str, ...]
    source_fingerprint: str
    evidence_fingerprint: str
    product_snapshot_version: int
    manufacturer: str | None = None
    image_url: str | None = None
    background_job_id: str | None = None


class EvidenceAcquisitionService:
    """Acquire/reuse source evidence without depending on graph state."""

    def __init__(
        self,
        *,
        catalogue: ProductCatalogue,
        artifacts: ArtifactStore,
        web_tool: WebExtractionTool,
    ) -> None:
        self._catalogue = catalogue
        self._artifacts = artifacts
        self._web_tool = web_tool

    async def acquire(self, request: EvidenceAcquisitionRequest) -> EvidenceAcquisitionResult:
        work = RunStore(request.context, self._catalogue, self._artifacts)
        if (
            request.reuse_prior_work
            and request.prior_evidence_artifact_id
            and not request.refresh_requested
        ):
            return self._reuse_persisted(work, request)
        return await self._extract_fresh(work, request)

    def _reuse_persisted(
        self,
        work: RunStore,
        request: EvidenceAcquisitionRequest,
    ) -> EvidenceAcquisitionResult:
        prior_id = request.prior_evidence_artifact_id
        if prior_id is None:
            raise ValueError("prior evidence artifact is required for evidence reuse")
        package = work.load(prior_id, ProductKnowledgePackage)
        evidence_id = work.put_model(
            "evidence/product-knowledge.json",
            package,
            derived_from=(prior_id,),
        )
        source_urls = tuple(dict.fromkeys(item.final_url for item in package.acquired_sources))
        fingerprint = _package_fingerprint(package)
        work.event(
            "product.work_reused",
            f"Reused {len(package.evidence)} persisted evidence records without crawling again.",
            metadata={
                "seededFromRunId": request.seeded_from_run_id,
                "priorEvidenceArtifactId": prior_id,
                "evidenceArtifactId": evidence_id,
            },
        )
        snapshot = update_product_snapshot(
            self._catalogue,
            request.context,
            ProductWorkStage.EVIDENCE,
            expected_version=request.product_snapshot_version,
            source_generation=request.source_generation,
            template_keys=request.target_submodels,
            evidence_artifact_id=evidence_id,
            reviewed_mapping_artifact_id=request.reviewed_mapping_artifact_id,
            source_fingerprint=fingerprint,
            evidence_fingerprint=fingerprint,
        )
        return EvidenceAcquisitionResult(
            evidence_artifact_id=evidence_id,
            product_name=package.product_name,
            known_source_urls=source_urls,
            source_fingerprint=fingerprint,
            evidence_fingerprint=fingerprint,
            product_snapshot_version=snapshot.version,
        )

    async def _extract_fresh(
        self,
        work: RunStore,
        request: EvidenceAcquisitionRequest,
    ) -> EvidenceAcquisitionResult:
        total_started = perf_counter()
        crawl_started = perf_counter()
        work.event(
            "crawl.seed.started",
            "Loading and extracting the product page before mapping begins.",
            metadata={"url": request.product_url, "activityKey": "seed-crawl"},
        )
        incoming = await self._web_tool.extract(request.product_url)
        crawl_duration_ms = round((perf_counter() - crawl_started) * 1000, 2)
        incoming = incoming.model_copy(update={"product_id": work.product_id})
        prior_id = request.prior_evidence_artifact_id
        package = (
            incoming
            if not prior_id
            else merge_packages(work.load(prior_id, ProductKnowledgePackage), incoming)
        )

        persist_started = perf_counter()
        raw_ids = tuple(
            work.put_bytes(
                f"sources/{source.id}.html",
                source.rendered_html.encode(),
                content_type="text/html; charset=utf-8",
                derived_from=(prior_id,) if prior_id else (),
            )
            for source in incoming.acquired_sources
        )
        markdown_ids = tuple(
            work.put_bytes(
                f"extraction/seed/{source.id}/markdown.md",
                source.markdown.encode(),
                content_type="text/markdown; charset=utf-8",
                derived_from=raw_ids,
            )
            for source in incoming.acquired_sources
            if source.markdown
        )
        structured_ids = tuple(
            work.put_model(
                f"extraction/structured-page-{index}.json",
                page,
                derived_from=raw_ids,
            )
            for index, page in enumerate(incoming.extracted_pages, start=1)
        )
        source_assets = tuple(asset for page in incoming.extracted_pages for asset in page.assets)
        asset_manifest_id = (
            work.put_json(
                "extraction/assets.json",
                [
                    {
                        **asset.model_dump(mode="json", by_alias=True),
                        "acquisition": "deferred",
                    }
                    for asset in source_assets
                ],
                derived_from=raw_ids,
            )
            if source_assets
            else None
        )
        evidence_id = work.put_model(
            "evidence/product-knowledge.json",
            package,
            derived_from=tuple(
                item
                for item in (
                    prior_id,
                    *raw_ids,
                    *markdown_ids,
                    *structured_ids,
                    asset_manifest_id,
                )
                if item
            ),
        )
        persistence_duration_ms = round((perf_counter() - persist_started) * 1000, 2)
        work.event(
            "crawl.seed.completed",
            "Completed the shallow seed crawl and persisted evidence without blocking on assets.",
            metadata={
                "durationMs": crawl_duration_ms,
                "sourceCount": len(incoming.acquired_sources),
                "evidenceCount": len(incoming.evidence),
                "deferredAssetCount": len(source_assets),
                "activityKey": "seed-crawl",
            },
        )

        source_urls = tuple(
            dict.fromkeys(
                (
                    *request.known_source_urls,
                    *(item.final_url for item in package.acquired_sources),
                )
            )
        )
        image_url = product_image_url(package)
        manufacturer = evidence_text(package, "manufacturer", "manufacturer name", "brand")
        product = self._catalogue.get_product(work.product_id, user_id=work.user_id)
        if product is None:
            raise KeyError(work.product_id)
        identifiers = discover_product_identifiers(
            package,
            manufacturer=manufacturer or product.manufacturer,
        )
        possible_duplicate_ids: list[str] = []
        for identifier in identifiers:
            try:
                self._catalogue.register_product_identifier(
                    identifier,
                    user_id=work.user_id,
                    run_id=work.run_id,
                )
            except ProductIdentifierConflict as error:
                possible_duplicate_ids.append(error.existing_product_id)
        manufacturer_product_id = next(
            (
                item.value
                for item in identifiers
                if item.scheme in {"manufacturer_part_number", "manufacturer_article_number"}
                and item.role.value == "identity"
            ),
            None,
        )
        self._catalogue.update_product(
            product.model_copy(
                update={
                    "name": package.product_name,
                    "manufacturer": manufacturer or product.manufacturer,
                    "manufacturer_product_id": (
                        product.manufacturer_product_id or manufacturer_product_id
                    ),
                    "image_url": image_url or product.image_url,
                }
            ),
            run_id=work.run_id,
        )
        if possible_duplicate_ids:
            work.event(
                "product.identity_match_detected",
                "A strong product identity already belongs to another durable product record.",
                metadata={
                    "possibleDuplicateProductIds": list(dict.fromkeys(possible_duplicate_ids)),
                    "automaticMerge": False,
                },
            )

        fingerprint = _package_fingerprint(package)
        work.event(
            "source.extracted",
            f"Persisted {len(package.evidence)} evidence records.",
            metadata={
                "artifactId": evidence_id,
                "sourceCount": len(source_urls),
                "evidenceCount": len(package.evidence),
                "persistenceDurationMs": persistence_duration_ms,
                "totalDurationMs": round((perf_counter() - total_started) * 1000, 2),
            },
        )
        job = self._catalogue.create_background_job(
            user_id=work.user_id,
            thread_id=request.context.thread_id,
            product_id=work.product_id,
            run_id=work.run_id,
            metadata={
                "seedUrl": request.product_url,
                "productName": package.product_name,
                "seedEvidenceArtifactId": evidence_id,
                "processedSources": 0,
                "totalSources": 0,
                "iteration": 0,
                "nextSourceIndex": 0,
                "phase": "queued",
                "sourceGeneration": request.source_generation,
            },
        )
        job_artifact_id = work.put_model(
            "background/deep-crawl-job.json",
            job,
            derived_from=(evidence_id,),
        )
        work.event(
            "research.queued",
            "Queued durable deep research without blocking initial mapping.",
            metadata={"jobId": job.id, "artifactId": job_artifact_id},
        )
        snapshot = update_product_snapshot(
            self._catalogue,
            request.context,
            ProductWorkStage.EVIDENCE,
            expected_version=request.product_snapshot_version,
            source_generation=request.source_generation,
            template_keys=request.target_submodels,
            evidence_artifact_id=evidence_id,
            source_fingerprint=fingerprint,
            evidence_fingerprint=fingerprint,
        )
        return EvidenceAcquisitionResult(
            evidence_artifact_id=evidence_id,
            product_name=package.product_name,
            manufacturer=manufacturer,
            image_url=image_url,
            known_source_urls=source_urls,
            source_fingerprint=fingerprint,
            evidence_fingerprint=fingerprint,
            background_job_id=job.id,
            product_snapshot_version=snapshot.version,
        )


def _package_fingerprint(package: ProductKnowledgePackage) -> str:
    return sha256_json(
        {
            "sources": [item.content_sha256 for item in package.acquired_sources],
            "evidence": [item.id for item in package.evidence],
        }
    )

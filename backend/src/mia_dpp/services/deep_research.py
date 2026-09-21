"""Durable deep-crawl processing that writes through catalogue and artifact services."""

from __future__ import annotations

from typing import Any

from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.mappings import MappingResult
from mia_dpp.domain.product import BackgroundJob, BackgroundJobStatus
from mia_dpp.domain.targets import TemplateIndex
from mia_dpp.errors import ExtractionError
from mia_dpp.storage.models import StoredArtifact
from mia_dpp.tools.mapping.coverage import coverage
from mia_dpp.tools.mapping.mapper import DeterministicWebsiteMapper
from mia_dpp.workflow.context import MiaContext
from mia_dpp.workflow.nodes_product import asset_workspace_path, merge_packages
from mia_dpp.workflow.workspace import RunWorkspace


def merge_mapping_results(existing: MappingResult, incoming: MappingResult) -> MappingResult:
    """Append outcomes for new evidence while preserving every existing human decision."""

    known = (
        {item.evidence_id for item in (*existing.mapped, *existing.ambiguous, *existing.rejected)}
        | set(existing.unmatched_evidence_ids)
        | set(existing.irrelevant_evidence_ids)
        | set(existing.rejected_evidence_ids)
    )

    def new_mappings(items: tuple[Any, ...]) -> tuple[Any, ...]:
        return tuple(item for item in items if item.evidence_id not in known)

    def new_ids(items: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(item for item in items if item not in known)

    return MappingResult(
        mapped=(*existing.mapped, *new_mappings(incoming.mapped)),
        ambiguous=(*existing.ambiguous, *new_mappings(incoming.ambiguous)),
        rejected=(*existing.rejected, *new_mappings(incoming.rejected)),
        unmatched_evidence_ids=(
            *existing.unmatched_evidence_ids,
            *new_ids(incoming.unmatched_evidence_ids),
        ),
        irrelevant_evidence_ids=(
            *existing.irrelevant_evidence_ids,
            *new_ids(incoming.irrelevant_evidence_ids),
        ),
        rejected_evidence_ids=(
            *existing.rejected_evidence_ids,
            *new_ids(incoming.rejected_evidence_ids),
        ),
        outcomes=(
            *existing.outcomes,
            *(item for item in incoming.outcomes if item.evidence_id not in known),
        ),
    )


class DeepResearchService:
    """Process one retry-safe deep-crawl job without mutating LangGraph checkpoints."""

    def __init__(self, context: MiaContext) -> None:
        self._context = context

    async def run(self, job_id: str, *, user_id: str) -> BackgroundJob:
        claimed = self._context.catalogue.claim_background_job(job_id, user_id=user_id)
        if claimed is None:
            existing = self._context.catalogue.get_background_job(job_id, user_id=user_id)
            if existing is None:
                raise KeyError(job_id)
            return existing
        work = self._workspace(claimed)
        work.event(
            "research.started",
            "Durable deep research started.",
            metadata={"jobId": claimed.id},
        )
        try:
            completed = await self._process(claimed, work)
        except Exception as error:
            detail = str(error)[:2000] or type(error).__name__
            work.event(
                "research.failed",
                "Deep research failed and remains retryable.",
                metadata={"jobId": claimed.id, "error": detail},
            )
            self._context.catalogue.finish_background_job(
                claimed.id,
                user_id=user_id,
                status=BackgroundJobStatus.FAILED,
                error=detail,
            )
            raise
        work.event(
            "research.completed",
            "Deep research completed and persisted its incremental evidence.",
            metadata={"jobId": claimed.id},
        )
        finished = self._context.catalogue.finish_background_job(
            claimed.id,
            user_id=user_id,
            status=BackgroundJobStatus.COMPLETED,
            metadata=completed,
        )
        work.put_model("background/deep-crawl-job.json", finished)
        return finished

    async def _process(self, job: BackgroundJob, work: RunWorkspace) -> dict[str, Any]:
        seed_id = str(job.metadata["seedEvidenceArtifactId"])
        package = work.load(seed_id, ProductKnowledgePackage)
        links = await self._context.web_tool.select_deep_sources(
            seed_url=str(job.metadata["seedUrl"]),
            product_name=str(job.metadata["productName"]),
        )
        sources_id = work.put_json(
            "research/sources.json",
            {
                "jobId": job.id,
                "seedUrl": job.metadata["seedUrl"],
                "sources": [
                    {"url": item.url, "text": item.text, "title": item.title} for item in links
                ],
            },
            derived_from=(seed_id,),
        )
        self._context.catalogue.update_background_job(
            job.id,
            user_id=job.user_id,
            metadata={"totalSources": len(links), "sourcesArtifactId": sources_id},
        )

        known_urls = {item.final_url for item in package.acquired_sources}
        processed = 0
        added_ids: set[str] = set()
        latest_evidence_id = seed_id
        for link in links:
            if link.url in known_urls:
                processed += 1
                self._progress(job, work, processed, len(links))
                continue
            work.event(
                "research.source_discovered",
                "Deep research selected an additional manufacturer source.",
                metadata={"url": link.url, "jobId": job.id},
            )
            try:
                incoming = await self._context.web_tool.extract_source(link.url)
                source_ids = await self._persist_source(
                    work,
                    incoming,
                    discovered_from=str(job.metadata["seedUrl"]),
                )
            except ExtractionError as error:
                failure_id = work.put_json(
                    f"research/failures/{processed + 1}.json",
                    {"url": link.url, "error": str(error), "jobId": job.id},
                    derived_from=(sources_id,),
                )
                work.event(
                    "research.source_failed",
                    "An optional deep-research source could not be acquired.",
                    metadata={"url": link.url, "artifactId": failure_id},
                )
            else:
                before = {item.id for item in package.evidence}
                package = merge_packages(package, incoming, preserve_existing=True)
                added_ids.update(item.id for item in package.evidence if item.id not in before)
                latest_evidence_id = work.put_model(
                    "evidence/product-knowledge-research.json",
                    package,
                    derived_from=(latest_evidence_id, *source_ids),
                )
                known_urls.update(item.final_url for item in incoming.acquired_sources)
                work.event(
                    "research.evidence_added",
                    "Added "
                    f"{len({item.id for item in package.evidence} - before)} new evidence records.",
                    metadata={"url": link.url, "artifactId": latest_evidence_id},
                )
            processed += 1
            self._progress(
                job,
                work,
                processed,
                len(links),
                evidence_artifact_id=latest_evidence_id,
            )

        mapping_metadata = await self._map_new_evidence(
            work,
            package,
            added_ids=added_ids,
            evidence_artifact_id=latest_evidence_id,
        )
        return {
            "processedSources": processed,
            "totalSources": len(links),
            "researchEvidenceArtifactId": latest_evidence_id,
            "newEvidenceCount": len(added_ids),
            **mapping_metadata,
        }

    async def _persist_source(
        self,
        work: RunWorkspace,
        package: ProductKnowledgePackage,
        *,
        discovered_from: str,
    ) -> tuple[str, ...]:
        artifact_ids: list[str] = []
        for source in package.acquired_sources:
            prefix = f"research/{source.id}"
            html_id = work.put_bytes(
                f"{prefix}/rendered.html",
                source.rendered_html.encode(),
                content_type="text/html; charset=utf-8",
            )
            artifact_ids.append(html_id)
            if source.markdown:
                artifact_ids.append(
                    work.put_bytes(
                        f"{prefix}/markdown.md",
                        source.markdown.encode(),
                        content_type="text/markdown; charset=utf-8",
                        derived_from=(html_id,),
                    )
                )
            work.put_json(
                f"{prefix}/source.json",
                {
                    "sourceUrl": source.final_url,
                    "sourceType": "website",
                    "acquiredAt": source.acquired_at,
                    "contentSha256": source.content_sha256,
                    "discoveredFrom": discovered_from,
                },
                derived_from=(html_id,),
            )
        for index, page in enumerate(package.extracted_pages, start=1):
            structured_id = work.put_model(
                f"research/{package.acquired_sources[0].id}/structured-{index}.json",
                page,
                derived_from=tuple(artifact_ids),
            )
            artifact_ids.append(structured_id)
            for asset in page.assets:
                try:
                    downloaded = await self._context.web_tool.download_source(asset.url)
                    asset_id = work.put_bytes(
                        asset_workspace_path(asset, downloaded.media_type),
                        downloaded.content,
                        content_type=downloaded.media_type,
                        derived_from=(structured_id,),
                    )
                    artifact_ids.append(asset_id)
                except Exception as error:
                    work.put_json(
                        f"research/{package.acquired_sources[0].id}/asset-errors/"
                        f"{len(artifact_ids)}.json",
                        {"url": asset.url, "error": str(error)[:2000]},
                        derived_from=(structured_id,),
                    )
        artifact_ids.append(
            work.put_model(
                f"research/{package.acquired_sources[0].id}/evidence.json",
                package,
                derived_from=tuple(artifact_ids),
            )
        )
        work.event(
            "research.source_acquired",
            "Persisted one deep-research source and its extracted artifacts.",
            metadata={"url": package.acquired_sources[0].final_url},
        )
        return tuple(artifact_ids)

    async def _map_new_evidence(
        self,
        work: RunWorkspace,
        package: ProductKnowledgePackage,
        *,
        added_ids: set[str],
        evidence_artifact_id: str,
    ) -> dict[str, Any]:
        if not added_ids or self._context.semantic_mapper is None:
            return {}
        targets_artifact = self._latest_artifact(work, "mapping/targets.json")
        if targets_artifact is None:
            return {"mappingRefreshPending": True}
        index = work.load(targets_artifact.id, TemplateIndex)
        incremental = package.model_copy(
            update={"evidence": tuple(item for item in package.evidence if item.id in added_ids)}
        )
        deterministic = await DeterministicWebsiteMapper(
            self._context.templates,
            index,
        ).propose(incremental.evidence)
        semantic = await self._context.semantic_mapper.map(incremental, index, deterministic)
        mapped = self._context.mapping_review.apply_semantic_run(
            incremental,
            deterministic,
            index,
            semantic,
        )
        mapping_id = work.put_model(
            "mapping/research-new-evidence.json",
            mapped,
            derived_from=(evidence_artifact_id, targets_artifact.id),
        )
        current_artifact = self._latest_mapping_artifact(work)
        integrated = (
            merge_mapping_results(work.load(current_artifact.id, MappingResult), mapped)
            if current_artifact is not None
            else mapped
        )
        integrated_mapping_id = work.put_model(
            "mapping/research-integrated.json",
            integrated,
            derived_from=tuple(
                item
                for item in (current_artifact.id if current_artifact else None, mapping_id)
                if item
            ),
        )
        coverage_id = work.put_model(
            "coverage/research-integrated.json",
            coverage(package, index, mapping_result=integrated),
            derived_from=(integrated_mapping_id,),
        )
        work.event(
            "research.coverage_updated",
            "Mapped new deep-research evidence and prepared it for safe workflow integration.",
            metadata={
                "mappingArtifactId": integrated_mapping_id,
                "coverageArtifactId": coverage_id,
            },
        )
        return {
            "newMappingArtifactId": mapping_id,
            "integratedMappingArtifactId": integrated_mapping_id,
            "researchCoverageArtifactId": coverage_id,
            "semanticModelRequests": semantic.metrics.model_requests,
        }

    def _latest_artifact(self, work: RunWorkspace, key: str) -> StoredArtifact | None:
        artifacts = self._context.catalogue.list_artifacts(
            run_id=work.run_id,
            user_id=work.user_id,
        )
        return next((item for item in reversed(artifacts) if item.key == key), None)

    def _latest_mapping_artifact(self, work: RunWorkspace) -> StoredArtifact | None:
        artifacts = self._context.catalogue.list_artifacts(
            run_id=work.run_id,
            user_id=work.user_id,
        )
        keys = {"mapping/reviewed.json", "mapping/mapping.json"}
        return next((item for item in reversed(artifacts) if item.key in keys), None)

    def _progress(
        self,
        job: BackgroundJob,
        work: RunWorkspace,
        processed: int,
        total: int,
        *,
        evidence_artifact_id: str | None = None,
    ) -> None:
        metadata: dict[str, Any] = {"processedSources": processed, "totalSources": total}
        if evidence_artifact_id:
            metadata["researchEvidenceArtifactId"] = evidence_artifact_id
        updated = self._context.catalogue.update_background_job(
            job.id,
            user_id=job.user_id,
            metadata=metadata,
        )
        work.put_model(
            "background/deep-crawl-job.json",
            updated,
            derived_from=(evidence_artifact_id,) if evidence_artifact_id else (),
        )

    def _workspace(self, job: BackgroundJob) -> RunWorkspace:
        return RunWorkspace(
            {
                "user_id": job.user_id,
                "thread_id": job.thread_id,
                "product_id": job.product_id,
                "run_id": job.run_id,
            },
            self._context,
        )

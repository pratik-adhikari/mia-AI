"""Durable, resumable deep-crawl processing over bounded source batches."""

from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.mappings import MappingResult
from mia_dpp.domain.product import BackgroundJob, BackgroundJobStatus
from mia_dpp.domain.targets import TemplateIndex
from mia_dpp.storage.models import StoredArtifact
from mia_dpp.tools.mapping.coverage import coverage
from mia_dpp.tools.mapping.mapper import DeterministicWebsiteMapper
from mia_dpp.tools.web.models import SourceLink
from mia_dpp.workflow.context import MiaContext
from mia_dpp.workflow.nodes_product import merge_packages
from mia_dpp.workflow.workspace import RunWorkspace

# Keep each Vercel worker invocation comfortably below the platform request ceiling.
# Telemetry is persisted for every batch so this can later become data-driven/configurable.
_SOURCE_BATCH_SIZE = 2


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
    """Advance one catalogue-owned crawl job by one retry-safe bounded batch."""

    def __init__(self, context: MiaContext) -> None:
        self._context = context

    async def run(self, job_id: str, *, user_id: str) -> BackgroundJob:
        claimed = self._context.catalogue.claim_background_job(job_id, user_id=user_id)
        if claimed is None:
            existing = self._context.catalogue.get_background_job(job_id, user_id=user_id)
            if existing is None:
                raise KeyError(job_id)
            return existing

        work: RunWorkspace | None = None
        iteration = int(claimed.metadata.get("iteration", 0)) + 1
        batch_started = perf_counter()
        try:
            work = self._workspace(claimed)
            work.event(
                "research.batch.started",
                f"Deep research batch {iteration} started.",
                metadata={"jobId": claimed.id, "iteration": iteration},
            )
            metadata, complete = await self._process_batch(claimed, work, iteration=iteration)
        except Exception as error:
            detail = str(error)[:2000] or type(error).__name__
            if work is not None:
                try:
                    work.event(
                        "research.failed",
                        "Deep research batch failed and remains retryable.",
                        metadata={
                            "jobId": claimed.id,
                            "iteration": iteration,
                            "durationMs": round((perf_counter() - batch_started) * 1000, 2),
                            "error": detail,
                        },
                    )
                except Exception:
                    # Recovery may have fenced the old run. Job terminalization must still happen.
                    pass
            self._context.catalogue.finish_background_job(
                claimed.id,
                user_id=user_id,
                status=BackgroundJobStatus.FAILED,
                error=detail,
            )
            raise

        duration_ms = round((perf_counter() - batch_started) * 1000, 2)
        metadata = {**metadata, "iteration": iteration, "lastBatchDurationMs": duration_ms}
        work.event(
            "research.batch.completed",
            f"Deep research batch {iteration} completed.",
            metadata={
                "jobId": claimed.id,
                "iteration": iteration,
                "durationMs": duration_ms,
                "processedSources": metadata.get("processedSources", 0),
                "totalSources": metadata.get("totalSources", 0),
                "newEvidenceCount": metadata.get("lastBatchNewEvidenceCount", 0),
                "complete": complete,
            },
        )

        if complete:
            work.event(
                "research.completed",
                "Deep research completed and persisted its incremental evidence.",
                metadata={"jobId": claimed.id, "iterations": iteration},
            )
            finished = self._context.catalogue.finish_background_job(
                claimed.id,
                user_id=user_id,
                status=BackgroundJobStatus.COMPLETED,
                metadata={**metadata, "phase": "completed"},
            )
        else:
            finished = self._context.catalogue.requeue_background_job(
                claimed.id,
                user_id=user_id,
                metadata={**metadata, "phase": "queued"},
            )

        # If recovery superseded this job while the batch was running, do not publish job metadata
        # through the fenced old workspace. The replacement job remains the durable continuation.
        if finished.status in {
            BackgroundJobStatus.CANCELLED,
            BackgroundJobStatus.FAILED,
        }:
            return finished
        work.put_model(
            "background/deep-crawl-job.json",
            finished,
            derived_from=(
                (str(metadata["researchEvidenceArtifactId"]),)
                if metadata.get("researchEvidenceArtifactId")
                else ()
            ),
        )
        return finished

    async def _process_batch(
        self,
        job: BackgroundJob,
        work: RunWorkspace,
        *,
        iteration: int,
    ) -> tuple[dict[str, Any], bool]:
        seed_id = str(job.metadata["seedEvidenceArtifactId"])
        evidence_id = str(job.metadata.get("researchEvidenceArtifactId") or seed_id)
        package = work.load(evidence_id, ProductKnowledgePackage)

        links, sources_id = self._frontier(job, work, package, seed_id)
        start_index = int(job.metadata.get("nextSourceIndex", 0))
        batch = links[start_index : start_index + _SOURCE_BATCH_SIZE]
        if not batch:
            return (
                {
                    "processedSources": start_index,
                    "totalSources": len(links),
                    "nextSourceIndex": start_index,
                    "researchEvidenceArtifactId": evidence_id,
                    "sourcesArtifactId": sources_id,
                    "lastBatchNewEvidenceCount": 0,
                },
                True,
            )

        results = await asyncio.gather(*(self._extract_timed(link) for link in batch))
        added_ids: set[str] = set()
        latest_evidence_id = evidence_id
        processed = start_index
        frontier = list(links)
        known_frontier_urls = {item.url for item in frontier}

        for link, incoming, error, duration_ms in results:
            work.event(
                "research.source.completed" if error is None else "research.source.failed",
                (
                    "Acquired one incremental research source."
                    if error is None
                    else "An optional incremental research source could not be acquired."
                ),
                metadata={
                    "url": link.url,
                    "iteration": iteration,
                    "durationMs": duration_ms,
                    **({"error": str(error)[:1000]} if error is not None else {}),
                },
            )
            if incoming is not None:
                source_ids = self._persist_source(
                    work,
                    incoming,
                    discovered_from=str(job.metadata["seedUrl"]),
                )
                for discovered in self._links_from_package(
                    incoming,
                    seed_url=str(job.metadata["seedUrl"]),
                ):
                    if discovered.url not in known_frontier_urls:
                        frontier.append(discovered)
                        known_frontier_urls.add(discovered.url)

                before = {item.id for item in package.evidence}
                package = merge_packages(package, incoming, preserve_existing=True)
                new_for_source = {item.id for item in package.evidence} - before
                added_ids.update(new_for_source)
                latest_evidence_id = work.put_model(
                    "evidence/product-knowledge-research.json",
                    package,
                    derived_from=(latest_evidence_id, *source_ids),
                )
                work.event(
                    "research.evidence_added",
                    f"Added {len(new_for_source)} new evidence records.",
                    metadata={
                        "url": link.url,
                        "artifactId": latest_evidence_id,
                        "iteration": iteration,
                    },
                )
            processed += 1

        mapping_started = perf_counter()
        mapping_metadata = await self._map_new_evidence(
            work,
            package,
            added_ids=added_ids,
            evidence_artifact_id=latest_evidence_id,
        )
        mapping_duration_ms = round((perf_counter() - mapping_started) * 1000, 2)
        next_index = start_index + len(batch)
        complete = next_index >= len(frontier)
        frontier_payload = [
            {"url": item.url, "text": item.text, "title": item.title} for item in frontier
        ]
        self._context.catalogue.update_background_job(
            job.id,
            user_id=job.user_id,
            metadata={
                "sourceCandidates": frontier_payload,
                "totalSources": len(frontier),
            },
        )
        return (
            {
                "processedSources": next_index,
                "totalSources": len(frontier),
                "nextSourceIndex": next_index,
                "researchEvidenceArtifactId": latest_evidence_id,
                "sourcesArtifactId": sources_id,
                "sourceCandidates": frontier_payload,
                "lastBatchNewEvidenceCount": len(added_ids),
                "lastBatchMappingDurationMs": mapping_duration_ms,
                **mapping_metadata,
            },
            complete,
        )

    def _frontier(
        self,
        job: BackgroundJob,
        work: RunWorkspace,
        package: ProductKnowledgePackage,
        seed_id: str,
    ) -> tuple[tuple[SourceLink, ...], str]:
        stored = job.metadata.get("sourceCandidates")
        sources_id = job.metadata.get("sourcesArtifactId")
        if isinstance(stored, list) and isinstance(sources_id, str):
            links = tuple(
                SourceLink(
                    url=str(item["url"]),
                    text=str(item.get("text") or ""),
                    title=str(item.get("title") or ""),
                )
                for item in stored
                if isinstance(item, dict) and item.get("url")
            )
            return links, sources_id

        discovery_started = perf_counter()
        links = self._links_from_package(
            package,
            seed_url=str(job.metadata["seedUrl"]),
        )
        discovery_duration_ms = round((perf_counter() - discovery_started) * 1000, 2)
        payload = [{"url": item.url, "text": item.text, "title": item.title} for item in links]
        sources_id = work.put_json(
            "research/sources.json",
            {
                "jobId": job.id,
                "seedUrl": job.metadata["seedUrl"],
                "sources": payload,
            },
            derived_from=(seed_id,),
        )
        self._context.catalogue.update_background_job(
            job.id,
            user_id=job.user_id,
            metadata={
                "totalSources": len(links),
                "sourceCandidates": payload,
                "sourcesArtifactId": sources_id,
                "discoveryDurationMs": discovery_duration_ms,
            },
        )
        work.event(
            "research.frontier.discovered",
            f"Persisted a frontier of {len(links)} candidate sources from retained HTML.",
            metadata={
                "durationMs": discovery_duration_ms,
                "sourceCount": len(links),
                "artifactId": sources_id,
            },
        )
        return links, sources_id

    @staticmethod
    def _links_from_package(
        package: ProductKnowledgePackage,
        *,
        seed_url: str,
    ) -> tuple[SourceLink, ...]:
        """Build a bounded same-domain frontier from HTML already acquired by Crawl4AI."""

        seed_host = (urlsplit(seed_url).hostname or "").removeprefix("www.")
        blocked = (
            "login",
            "cart",
            "privacy",
            "legal",
            "imprint",
            "facebook",
            "instagram",
            "linkedin",
        )
        useful = (
            "product",
            "technical",
            "datasheet",
            "download",
            "document",
            "manual",
            "spec",
        )
        candidates: dict[str, SourceLink] = {}
        for source in package.acquired_sources:
            soup = BeautifulSoup(source.rendered_html, "html.parser")
            for anchor in soup.select("a[href]"):
                href = str(anchor.get("href") or "").strip()
                if not href:
                    continue
                url = urljoin(source.final_url, href).split("#", 1)[0]
                parsed = urlsplit(url)
                host = (parsed.hostname or "").removeprefix("www.")
                if parsed.scheme not in {"http", "https"} or host != seed_host:
                    continue
                lowered = url.casefold()
                if any(token in lowered for token in blocked):
                    continue
                text = " ".join(anchor.get_text(" ", strip=True).split())
                title = str(anchor.get("title") or "").strip()
                relevance = f"{lowered} {text.casefold()} {title.casefold()}"
                if not any(token in relevance for token in useful):
                    continue
                candidates.setdefault(url, SourceLink(url=url, text=text, title=title))
                if len(candidates) >= 24:
                    break
            if len(candidates) >= 24:
                break
        return tuple(candidates.values())

    async def _extract_timed(
        self,
        link: SourceLink,
    ) -> tuple[SourceLink, ProductKnowledgePackage | None, Exception | None, float]:
        started = perf_counter()
        try:
            incoming = await self._context.web_tool.extract_source(link.url)
        except Exception as error:
            return link, None, error, round((perf_counter() - started) * 1000, 2)
        return link, incoming, None, round((perf_counter() - started) * 1000, 2)

    def _persist_source(
        self,
        work: RunWorkspace,
        package: ProductKnowledgePackage,
        *,
        discovered_from: str,
    ) -> tuple[str, ...]:
        """Persist text evidence immediately; keep binary assets deferred to later iterations."""

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
            if page.assets:
                artifact_ids.append(
                    work.put_json(
                        f"research/{package.acquired_sources[0].id}/assets-{index}.json",
                        [
                            {
                                **asset.model_dump(mode="json", by_alias=True),
                                "acquisition": "deferred",
                            }
                            for asset in page.assets
                        ],
                        derived_from=(structured_id,),
                    )
                )

        artifact_ids.append(
            work.put_model(
                f"research/{package.acquired_sources[0].id}/evidence.json",
                package,
                derived_from=tuple(artifact_ids),
            )
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
                "newEvidenceCount": len(added_ids),
                "semanticModelRequests": semantic.metrics.model_requests,
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
        keys = {
            "mapping/reviewed.json",
            "mapping/mapping.json",
            "mapping/research-integrated.json",
        }
        return next((item for item in reversed(artifacts) if item.key in keys), None)

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

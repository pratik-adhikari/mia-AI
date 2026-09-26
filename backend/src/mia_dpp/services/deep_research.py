"""Durable, resumable deep-crawl processing over bounded source batches."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from time import perf_counter
from typing import Any, Literal
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field

from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.evidence_merge import merge_packages
from mia_dpp.domain.mappings import MappingResult
from mia_dpp.domain.product import BackgroundJob, BackgroundJobStatus
from mia_dpp.domain.targets import TemplateIndex
from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.runtime.run_context import RunContext
from mia_dpp.runtime.run_store import RunStore
from mia_dpp.semantic.decision_policy import DecisionPolicySettings
from mia_dpp.semantic.jev import JevDecisionClient
from mia_dpp.semantic.jev_mapping import map_new_jev_evidence
from mia_dpp.semantic.models import ContextScope
from mia_dpp.storage.base import ArtifactStore
from mia_dpp.storage.models import StoredArtifact
from mia_dpp.tools.mapping.coverage import coverage
from mia_dpp.tools.mapping.mapper import DeterministicWebsiteMapper
from mia_dpp.tools.mapping.models import SemanticMapper
from mia_dpp.tools.mapping.review import MappingReviewService
from mia_dpp.tools.web.models import SourceLink
from mia_dpp.tools.web.tool import WebExtractionTool

# Keep each Vercel worker invocation comfortably below the platform request ceiling.
# Telemetry is persisted for every batch so this can later become data-driven/configurable.
_SOURCE_BATCH_SIZE = 2
_SOURCE_TIMEOUT_SECONDS = 30
_IDENTITY_WORD = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")


def _words(value: str) -> frozenset[str]:
    compounds = _IDENTITY_WORD.findall(value.casefold())
    return frozenset(
        token for compound in compounds
        for token in (compound, *re.split(r"[-_]", compound))
    )


class CrawlScopeConfig(BaseModel):
    """Product crawl boundaries loaded from the editable JSON policy."""

    model_config = ConfigDict(extra="forbid")

    max_total_sources: int = Field(ge=1)
    max_links_per_page: int = Field(ge=1)
    off_subtree_mode: Literal["one_hop", "disabled"]
    min_identity_token_length: int = Field(ge=1)
    identity_stopwords: tuple[str, ...]

    @classmethod
    def load(cls) -> CrawlScopeConfig:
        return cls.model_validate_json(
            Path(__file__).resolve().parents[1].joinpath("crawl_scope.json").read_text()
        )


def _url_key(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    return (
        (parsed.hostname or "").removeprefix("www.").casefold(),
        unquote(parsed.path).rstrip("/").casefold() or "/",
    )


def _is_subtree(url: str, seed_url: str) -> bool:
    host, path = _url_key(url)
    seed_host, seed_path = _url_key(seed_url)
    return host == seed_host and path.startswith(seed_path.rstrip("/") + "/")


def _identity_tokens(seed_url: str, config: CrawlScopeConfig) -> frozenset[str]:
    path = unquote(urlsplit(seed_url).path).casefold().rstrip("/")
    segments = path.split("/")[-2:]
    brand = (urlsplit(seed_url).hostname or "").removeprefix("www.").split(".")[0].casefold()
    blocked = set(config.identity_stopwords) | {brand}
    return frozenset(
        token
        for segment in segments
        for token in _words(segment)
        if len(token) >= config.min_identity_token_length and token not in blocked
    )


def _is_related_link(link: SourceLink, identity_tokens: frozenset[str]) -> bool:
    # Query parameters such as backUrl can contain the seed product on an unrelated page.
    # Compare only the destination path and the visible anchor text/title.
    parsed = urlsplit(link.url)
    subject = " ".join((unquote(parsed.path), link.text, link.title)).casefold()
    words = _words(subject)
    return bool(identity_tokens & words)


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

    def __init__(
        self,
        *,
        catalogue: ProductCatalogue,
        artifacts: ArtifactStore,
        templates: OfficialTemplateRepository,
        web_tool: WebExtractionTool,
        mapping_review: MappingReviewService,
        semantic_mapper: SemanticMapper | None,
        jev_decider: JevDecisionClient | None,
        jev_mapping_enabled: bool,
        jev_routing_scopes: tuple[ContextScope, ...],
        jev_routing_max_concurrency: int,
        jev_decision_policy: DecisionPolicySettings,
    ) -> None:
        self._catalogue = catalogue
        self._artifacts = artifacts
        self._templates = templates
        self._web_tool = web_tool
        self._mapping_review = mapping_review
        self._semantic_mapper = semantic_mapper
        self._jev_decider = jev_decider
        self._jev_mapping_enabled = jev_mapping_enabled
        self._jev_routing_scopes = jev_routing_scopes
        self._jev_routing_max_concurrency = jev_routing_max_concurrency
        self._jev_decision_policy = jev_decision_policy
        self._crawl_scope = CrawlScopeConfig.load()

    async def run(self, job_id: str, *, user_id: str) -> BackgroundJob:
        claimed = self._catalogue.claim_background_job(job_id, user_id=user_id)
        if claimed is None:
            existing = self._catalogue.get_background_job(job_id, user_id=user_id)
            if existing is None:
                raise KeyError(job_id)
            return existing

        work: RunStore | None = None
        iteration = int(claimed.metadata.get("iteration", 0)) + 1
        batch_started = perf_counter()
        try:
            work = self._run_store(claimed)
            work.event(
                "research.batch.started",
                f"Deep research batch {iteration} started.",
                metadata={
                    "jobId": claimed.id,
                    "iteration": iteration,
                    "activityKey": f"{claimed.id}:research-batch:{iteration}",
                },
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
            self._catalogue.finish_background_job(
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
                "activityKey": f"{claimed.id}:research-batch:{iteration}",
            },
        )

        if complete:
            work.event(
                "research.completed",
                "Deep research completed and persisted its incremental evidence.",
                metadata={"jobId": claimed.id, "iterations": iteration},
            )
            finished = self._catalogue.finish_background_job(
                claimed.id,
                user_id=user_id,
                status=BackgroundJobStatus.COMPLETED,
                metadata={**metadata, "phase": "completed"},
            )
        else:
            finished = self._catalogue.requeue_background_job(
                claimed.id,
                user_id=user_id,
                metadata={
                    **metadata,
                    # Human review belongs to the foreground mapping workflow. It must not
                    # back-pressure source acquisition or background mapping of later batches.
                    "phase": (
                        "waiting_for_targets"
                        if metadata.get("mappingRefreshPending") else "queued"
                    ),
                    "awaitingHumanReview": False,
                },
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
        work: RunStore,
        *,
        iteration: int,
    ) -> tuple[dict[str, Any], bool]:
        seed_id = str(job.metadata["seedEvidenceArtifactId"])
        evidence_id = str(job.metadata.get("researchEvidenceArtifactId") or seed_id)
        package = work.load(evidence_id, ProductKnowledgePackage)

        links, sources_id, start_index = self._frontier(job, work, seed_id)
        batch = links[start_index : start_index + _SOURCE_BATCH_SIZE]
        if not batch:
            # A source batch may have finished before the template index was available.
            # Retry its retained evidence here, even when the crawl frontier is empty.
            mapping_metadata = await self._map_new_evidence(
                work,
                package,
                added_ids=set(),
                evidence_artifact_id=evidence_id,
                seed_evidence_artifact_id=seed_id,
            )
            return (
                {
                    "processedSources": start_index,
                    "totalSources": len(links),
                    "nextSourceIndex": start_index,
                    "researchEvidenceArtifactId": evidence_id,
                    "sourcesArtifactId": sources_id,
                    "sourceCandidates": [
                        {"url": item.url, "text": item.text, "title": item.title}
                        for item in links
                    ],
                    "lastBatchNewEvidenceCount": 0,
                    **mapping_metadata,
                },
                not mapping_metadata.get("mappingRefreshPending", False),
            )

        for link in batch:
            work.event(
                "research.source.started",
                f"Crawling source: {link.title or link.url}",
                metadata={"url": link.url, "iteration": iteration, "activityKey": link.url},
            )
        results = await asyncio.gather(
            *(self._extract_timed(link, seed_url=str(job.metadata["seedUrl"])) for link in batch)
        )
        added_ids: set[str] = set()
        latest_evidence_id = evidence_id
        processed = start_index
        frontier = list(links)
        known_frontier_urls = {_url_key(item.url) for item in frontier}

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
                    "activityKey": link.url,
                    **({"error": str(error)[:1000]} if error is not None else {}),
                },
            )
            if incoming is not None:
                source_ids = self._persist_source(
                    work,
                    incoming,
                    discovered_from=str(job.metadata["seedUrl"]),
                )
                seed_url = str(job.metadata["seedUrl"])
                if _is_subtree(link.url, seed_url):
                    for discovered in self._links_from_package(
                        incoming, seed_url=seed_url,
                    ):
                        candidate_key = _url_key(discovered.url)
                        if (
                            candidate_key not in known_frontier_urls
                            and len(frontier) < self._crawl_scope.max_total_sources
                        ):
                            frontier.append(discovered)
                            known_frontier_urls.add(candidate_key)

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

        next_index = start_index + len(batch)
        complete = next_index >= len(frontier)
        frontier_payload = [
            {"url": item.url, "text": item.text, "title": item.title} for item in frontier
        ]
        # Publish acquired evidence before the comparatively slow semantic model call. The
        # workspace can then display new source facts while mapping for this batch is running.
        self._catalogue.update_background_job(
            job.id,
            user_id=job.user_id,
            metadata={
                "processedSources": next_index,
                "totalSources": len(frontier),
                "nextSourceIndex": next_index,
                "researchEvidenceArtifactId": latest_evidence_id,
                "sourceCandidates": frontier_payload,
                "lastBatchNewEvidenceCount": len(added_ids),
                "phase": "mapping" if added_ids else "crawling",
            },
        )
        mapping_started = perf_counter()
        mapping_metadata = await self._map_new_evidence(
            work,
            package,
            added_ids=added_ids,
            evidence_artifact_id=latest_evidence_id,
            seed_evidence_artifact_id=seed_id,
        )
        if mapping_metadata.get("mappingRefreshPending"):
            complete = False
        mapping_duration_ms = round((perf_counter() - mapping_started) * 1000, 2)
        self._catalogue.update_background_job(
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
        work: RunStore,
        seed_id: str,
    ) -> tuple[tuple[SourceLink, ...], str, int]:
        stored = job.metadata.get("sourceCandidates")
        sources_id = job.metadata.get("sourcesArtifactId")
        seed_url = str(job.metadata["seedUrl"])
        if isinstance(stored, list) and isinstance(sources_id, str):
            seed_package = work.load(seed_id, ProductKnowledgePackage)
            direct_links = self._links_from_package(
                seed_package, seed_url=seed_url, allow_off_subtree=True,
            )
            allowed_off_subtree = {_url_key(item.url) for item in direct_links}
            old_index = int(job.metadata.get("nextSourceIndex", 0))
            links: list[SourceLink] = []
            seen: set[tuple[str, str]] = set()
            next_index = 0
            for position, item in enumerate(stored):
                if not isinstance(item, dict) or not item.get("url"):
                    continue
                link = SourceLink(
                    url=str(item["url"]),
                    text=str(item.get("text") or ""),
                    title=str(item.get("title") or ""),
                )
                candidate_key = _url_key(link.url)
                if (
                    candidate_key in seen
                    or len(links) >= self._crawl_scope.max_total_sources
                    or not (
                        _is_subtree(link.url, seed_url)
                        or candidate_key in allowed_off_subtree
                    )
                ):
                    continue
                links.append(link)
                seen.add(candidate_key)
                if position < old_index:
                    next_index += 1
            if len(links) != len(stored) or next_index != old_index:
                payload = [
                    {"url": item.url, "text": item.text, "title": item.title}
                    for item in links
                ]
                sources_id = work.put_json(
                    "research/sources-scoped.json",
                    {"jobId": job.id, "seedUrl": seed_url, "sources": payload},
                    derived_from=(sources_id,),
                )
                self._catalogue.update_background_job(
                    job.id,
                    user_id=job.user_id,
                    metadata={
                        "sourceCandidates": payload,
                        "totalSources": len(links),
                        "nextSourceIndex": next_index,
                        "processedSources": next_index,
                        "sourcesArtifactId": sources_id,
                    },
                )
            return tuple(links), sources_id, next_index

        discovery_started = perf_counter()
        links = self._links_from_package(
            work.load(seed_id, ProductKnowledgePackage),
            seed_url=seed_url,
            allow_off_subtree=True,
        )
        discovery_duration_ms = round((perf_counter() - discovery_started) * 1000, 2)
        payload = [{"url": item.url, "text": item.text, "title": item.title} for item in links]
        sources_id = work.put_json(
            "research/sources.json",
            {
                "jobId": job.id,
                "seedUrl": seed_url,
                "sources": payload,
            },
            derived_from=(seed_id,),
        )
        self._catalogue.update_background_job(
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
        return links, sources_id, 0

    def _links_from_package(
        self,
        package: ProductKnowledgePackage,
        *,
        seed_url: str,
        allow_off_subtree: bool = False,
    ) -> tuple[SourceLink, ...]:
        """Accept descendants, plus product-matching links directly from the seed page."""

        seed_host = _url_key(seed_url)[0]
        identity_tokens = _identity_tokens(seed_url, self._crawl_scope)
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
        candidates: dict[tuple[str, str], SourceLink] = {}
        for source_index, source in enumerate(package.acquired_sources):
            if not allow_off_subtree and not _is_subtree(source.final_url, seed_url):
                continue
            soup = BeautifulSoup(source.rendered_html, "html.parser")
            for anchor in soup.select("a[href]"):
                href = str(anchor.get("href") or "").strip()
                if not href:
                    continue
                url = urljoin(source.final_url, href).split("#", 1)[0]
                parsed = urlsplit(url)
                host = (parsed.hostname or "").removeprefix("www.").casefold()
                if parsed.scheme not in {"http", "https"} or host != seed_host:
                    continue
                if any(token in parsed.path.casefold() for token in blocked):
                    continue
                text = " ".join(anchor.get_text(" ", strip=True).split())
                title = str(anchor.get("title") or "").strip()
                link = SourceLink(url=url, text=text, title=title)
                if _url_key(url) == _url_key(seed_url):
                    continue
                if not _is_subtree(url, seed_url) and not (
                    allow_off_subtree
                    and source_index == 0
                    and self._crawl_scope.off_subtree_mode == "one_hop"
                    and _is_related_link(link, identity_tokens)
                ):
                    continue
                candidates.setdefault(_url_key(url), link)
                if len(candidates) >= self._crawl_scope.max_links_per_page:
                    break
            if len(candidates) >= self._crawl_scope.max_links_per_page:
                break
        return tuple(candidates.values())

    async def _extract_timed(
        self,
        link: SourceLink,
        *,
        seed_url: str,
    ) -> tuple[SourceLink, ProductKnowledgePackage | None, Exception | None, float]:
        started = perf_counter()
        try:
            incoming = await asyncio.wait_for(
                self._web_tool.extract_source(link.url),
                timeout=_SOURCE_TIMEOUT_SECONDS,
            )
            for source in incoming.acquired_sources:
                if _is_subtree(link.url, seed_url):
                    allowed = _is_subtree(source.final_url, seed_url)
                else:
                    allowed = _url_key(source.final_url) == _url_key(link.url)
                if not allowed:
                    raise ValueError(
                        "source redirected outside its permitted product crawl scope: "
                        f"{source.final_url}"
                    )
            if not _is_subtree(link.url, seed_url):
                identity_tokens = _identity_tokens(seed_url, self._crawl_scope)
                if not identity_tokens.intersection(_words(incoming.product_name)):
                    raise ValueError(
                        f"off-subtree source does not identify the seed product: {link.url}"
                    )
        except Exception as error:
            return link, None, error, round((perf_counter() - started) * 1000, 2)
        return link, incoming, None, round((perf_counter() - started) * 1000, 2)

    def _persist_source(
        self,
        work: RunStore,
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
        work: RunStore,
        package: ProductKnowledgePackage,
        *,
        added_ids: set[str],
        evidence_artifact_id: str,
        seed_evidence_artifact_id: str,
    ) -> dict[str, Any]:
        if (
            self._semantic_mapper is None
            and not self._jev_mapping_enabled
        ):
            return {}
        current_artifact = self._latest_mapping_artifact(work)
        current_mapping = (
            work.load(current_artifact.id, MappingResult) if current_artifact is not None else None
        )
        covered_ids = (
            {item.evidence_id for item in current_mapping.outcomes}
            if current_mapping is not None
            else set()
        )
        seed_package = work.load(seed_evidence_artifact_id, ProductKnowledgePackage)
        seed_ids = {item.id for item in seed_package.evidence}
        pending_ids = added_ids | {
            item.id for item in package.evidence if item.id not in seed_ids | covered_ids
        }
        if not pending_ids:
            return {"mappingRefreshPending": False}
        targets_artifact = self._latest_artifact(work, "mapping/targets.json")
        if targets_artifact is None:
            return {"mappingRefreshPending": True}

        index = work.load(targets_artifact.id, TemplateIndex)
        work.event(
            "research.mapping.started",
            f"Mapping {len(pending_ids)} acquired evidence records, including any deferred batch.",
            metadata={
                "evidenceCount": len(pending_ids),
                "activityKey": evidence_artifact_id,
            },
        )
        incremental = package.model_copy(
            update={"evidence": tuple(item for item in package.evidence if item.id in pending_ids)}
        )
        if self._jev_mapping_enabled:
            if self._jev_decider is None:
                raise RuntimeError("Jev mapping requires a configured Jev decider")
            mapped, _, routing, policy = await map_new_jev_evidence(
                package=package,
                evidence_ids=pending_ids,
                index=index,
                templates=self._templates,
                decider=self._jev_decider,
                scopes=self._jev_routing_scopes,
                max_concurrency=self._jev_routing_max_concurrency,
                settings=self._jev_decision_policy,
            )
            routing_id = work.put_model(
                "semantic/research-jev-routing.json", routing,
                derived_from=(evidence_artifact_id, targets_artifact.id),
            )
            work.put_model(
                "semantic/research-jev-policy.json", policy,
                derived_from=(routing_id,),
            )
            model_requests = sum(
                not step.deterministic for trace in routing.traces for step in trace.steps
            )
        else:
            mapper = self._semantic_mapper
            if mapper is None:
                raise RuntimeError("semantic mapping requires a configured model")
            deterministic = await DeterministicWebsiteMapper(
                self._templates,
                index,
            ).propose(incremental.evidence)
            semantic = await mapper.map(incremental, index, deterministic)
            mapped = self._mapping_review.apply_semantic_run(
                incremental,
                deterministic,
                index,
                semantic,
            )
            model_requests = semantic.metrics.model_requests
        mapping_id = work.put_model(
            "mapping/research-new-evidence.json",
            mapped,
            derived_from=(evidence_artifact_id, targets_artifact.id),
        )
        integrated = (
            merge_mapping_results(current_mapping, mapped)
            if current_mapping is not None
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
                "newEvidenceCount": len(pending_ids),
                "semanticModelRequests": model_requests,
            },
        )
        work.event(
            "research.mapping.completed",
            f"Mapped {len(pending_ids)} acquired evidence records.",
            metadata={
                "evidenceCount": len(pending_ids),
                "semanticModelRequests": model_requests,
                "activityKey": evidence_artifact_id,
            },
        )
        return {
            "mappingRefreshPending": False,
            "newMappingArtifactId": mapping_id,
            "integratedMappingArtifactId": integrated_mapping_id,
            "researchCoverageArtifactId": coverage_id,
            "semanticModelRequests": model_requests,
        }

    def _latest_artifact(self, work: RunStore, key: str) -> StoredArtifact | None:
        artifacts = self._catalogue.list_artifacts(
            run_id=work.run_id,
            user_id=work.user_id,
        )
        return next((item for item in reversed(artifacts) if item.key == key), None)

    def _latest_mapping_artifact(self, work: RunStore) -> StoredArtifact | None:
        artifacts = self._catalogue.list_artifacts(
            run_id=work.run_id,
            user_id=work.user_id,
        )
        keys = {
            "mapping/reviewed.json",
            "mapping/mapping.json",
            "mapping/research-integrated.json",
        }
        return next((item for item in reversed(artifacts) if item.key in keys), None)

    def _run_store(self, job: BackgroundJob) -> RunStore:
        return RunStore(
            RunContext(
                user_id=job.user_id,
                thread_id=job.thread_id,
                product_id=job.product_id,
                run_id=job.run_id,
            ),
            self._catalogue,
            self._artifacts,
        )

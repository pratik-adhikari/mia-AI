"""Read-only application service for conversational product/work queries."""

from __future__ import annotations

import re

from pydantic import AwareDatetime, Field

from mia_dpp.domain.base import WireModel
from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.product import RunEvent, RunStatus
from mia_dpp.domain.product_work import ProductWorkStage
from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.storage.base import ArtifactStore


class WorkStatusView(WireModel):
    """Durable work status safe to expose to chat and APIs."""

    thread_id: str
    product_id: str | None = None
    product_name: str | None = None
    run_id: str | None = None
    run_status: RunStatus | None = None
    workflow_stage: ProductWorkStage | None = None
    workflow_generation: int = Field(default=0, ge=0)
    lease_live: bool = False
    started_at: AwareDatetime | None = None
    last_heartbeat_at: AwareDatetime | None = None
    human_review_pending: bool = False
    unresolved_required_count: int = Field(default=0, ge=0)
    last_error: str | None = None
    recent_events: tuple[RunEvent, ...] = ()


class EvidenceSearchHit(WireModel):
    """One exact source-backed fact returned to the conversation plane."""

    evidence_id: str
    label: str
    value: str
    unit: str | None = None
    context_path: tuple[str, ...] = ()
    source_uri: str
    excerpt: str | None = None
    score: int = Field(ge=0)


class ProductQueryService:
    """Query durable product knowledge without depending on LangGraph checkpoints."""

    def __init__(self, catalogue: ProductCatalogue, artifacts: ArtifactStore) -> None:
        self._catalogue = catalogue
        self._artifacts = artifacts

    def work_status(self, thread_id: str, *, user_id: str) -> WorkStatusView:
        thread = self._catalogue.get_thread(thread_id, user_id=user_id)
        if thread is None:
            raise KeyError(thread_id)
        runs = self._catalogue.list_runs_for_thread(thread_id, user_id=user_id)
        if not runs:
            return WorkStatusView(
                thread_id=thread_id,
                workflow_generation=thread.workflow_generation,
            )

        run = runs[-1]
        product = self._catalogue.get_product(run.product_id, user_id=user_id)
        snapshot = self._catalogue.get_product_work_snapshot(
            run.product_id,
            user_id=user_id,
        )
        events = self._catalogue.list_events(run.id, user_id=user_id)
        return WorkStatusView(
            thread_id=thread_id,
            product_id=run.product_id,
            product_name=(
                product.name
                if product is not None and product.name
                else (product.manufacturer_product_id if product is not None else None)
            ),
            run_id=run.id,
            run_status=run.status,
            workflow_stage=snapshot.workflow_stage if snapshot is not None else None,
            workflow_generation=run.workflow_generation,
            lease_live=self._catalogue.run_lease_is_live(run),
            started_at=run.started_at,
            last_heartbeat_at=run.last_heartbeat_at,
            human_review_pending=(
                snapshot.human_review_pending if snapshot is not None else False
            ),
            unresolved_required_count=(
                len(snapshot.unresolved_required_ids) if snapshot is not None else 0
            ),
            last_error=(snapshot.last_error if snapshot is not None else run.error),
            recent_events=events[-12:],
        )

    def search_evidence(
        self,
        thread_id: str,
        query: str,
        *,
        user_id: str,
        limit: int = 8,
    ) -> tuple[EvidenceSearchHit, ...]:
        if limit < 1:
            raise ValueError("evidence search limit must be at least one")
        status = self.work_status(thread_id, user_id=user_id)
        if status.product_id is None:
            return ()
        snapshot = self._catalogue.get_product_work_snapshot(
            status.product_id,
            user_id=user_id,
        )
        if snapshot is None or snapshot.evidence_artifact_id is None:
            return ()
        artifact = self._catalogue.get_artifact(
            snapshot.evidence_artifact_id,
            user_id=user_id,
        )
        if artifact is None:
            return ()
        package = ProductKnowledgePackage.model_validate_json(
            self._artifacts.get(artifact)
        )

        tokens = tuple(
            token
            for token in re.findall(r"[a-z0-9]+", query.casefold())
            if len(token) > 1
        )
        scored: list[EvidenceSearchHit] = []
        for record in package.evidence:
            label = record.source_label or record.predicate
            context = " ".join(record.context_path)
            value = str(record.value)
            haystack = " ".join(
                (
                    label,
                    record.predicate,
                    context,
                    value,
                    record.unit or "",
                )
            ).casefold()
            if tokens:
                matched = sum(token in haystack for token in tokens)
                if matched == 0:
                    continue
                exact_label = int(query.casefold().strip() in label.casefold())
                score = matched * 10 + exact_label * 5
            else:
                score = 1
            scored.append(
                EvidenceSearchHit(
                    evidence_id=record.id,
                    label=label,
                    value=value,
                    unit=record.unit,
                    context_path=record.context_path,
                    source_uri=record.source_uri,
                    excerpt=record.source_location.excerpt,
                    score=score,
                )
            )
        scored.sort(
            key=lambda item: (
                -item.score,
                len(item.context_path),
                item.label.casefold(),
                item.evidence_id,
            )
        )
        return tuple(scored[: min(limit, 25)])

    def recent_events(
        self,
        thread_id: str,
        *,
        user_id: str,
        limit: int = 12,
    ) -> tuple[RunEvent, ...]:
        if limit < 1:
            raise ValueError("event limit must be at least one")
        status = self.work_status(thread_id, user_id=user_id)
        if status.run_id is None:
            return ()
        events = self._catalogue.list_events(status.run_id, user_id=user_id)
        return events[-min(limit, 50) :]

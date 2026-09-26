"""Build the existing workspace API response from durable workflow state."""

from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel

from mia_dpp.aas.models import DppPackage
from mia_dpp.agent.models import (
    AgentResponse,
    AgentStatus,
    HumanRequest,
    HumanRequestKind,
    ProductStatus,
    ProductWork,
)
from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.mappings import MappingResult, SemanticReviewItem
from mia_dpp.domain.targets import TemplateIndex
from mia_dpp.persistence.workspace import WorkspaceView
from mia_dpp.capabilities.mapping import merge_mapping_results
from mia_dpp.workflow.context import MiaContext

ModelT = TypeVar("ModelT", bound=BaseModel)


class AgentResponseView:
    """Translate graph state + durable artifacts into the legacy workspace contract."""

    def __init__(self, context: MiaContext, workspace: WorkspaceView) -> None:
        self._context = context
        self._workspace = workspace

    def build(self, state: dict[str, Any], *, trace_offset: int = 0) -> AgentResponse:
        thread_id = str(state.get("thread_id", ""))
        user_id = str(state.get("user_id", "local-development"))
        view_state = self._with_durable_run(state)
        background_job = self._background_job(view_state)
        snapshot = self._product_snapshot(view_state)
        if snapshot is not None and snapshot.human_review_pending:
            view_state["review_required"] = True
            view_state.setdefault("mapping_cycle_id", snapshot.mapping_cycle_id)
            view_state["status"] = AgentStatus.AWAITING_REVIEW.value
        pending = self._pending_request(view_state)
        status = self._status(view_state, pending)
        return AgentResponse(
            thread_id=thread_id,
            reply=(
                pending.summary
                if pending is not None
                else str(state.get("reply") or "MIA persisted the current workflow progress.")
            ),
            status=status,
            decision_summary=str(state.get("decision_summary") or self._decision_summary(status)),
            company_candidates=tuple(state.get("company_candidates", ())),
            selected_company=state.get("selected_company"),
            product_candidates=tuple(state.get("product_candidates", ())),
            selected_product_ids=tuple(state.get("selected_product_ids", ())),
            current_product=self._current_product(view_state),
            pending_human_request=pending,
            trace_events=self._workspace.list_events(
                thread_id,
                trace_offset,
                user_id=user_id,
            ),
            artifact_count=len(self._workspace.list_artifacts(thread_id, user_id=user_id)),
            background_job_id=background_job.id if background_job else None,
        )

    def _with_durable_run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Restore product/run identity when a stale checkpoint omitted it."""
        if state.get("product_id") and state.get("run_id"):
            return dict(state)
        thread_id = str(state.get("thread_id", ""))
        if not thread_id:
            return dict(state)
        user_id = str(state.get("user_id", "local-development"))
        runs = self._context.catalogue.list_runs_for_thread(thread_id, user_id=user_id)
        if not runs:
            return dict(state)
        run_id = state.get("run_id")
        run = next((item for item in runs if item.id == run_id), None) if run_id else None
        run = run or runs[-1]
        restored = dict(state)
        if not restored.get("run_id"):
            restored["run_id"] = run.id
        if not restored.get("product_id"):
            restored["product_id"] = run.product_id
        return restored

    def _current_product(self, state: dict[str, Any]) -> ProductWork | None:
        product_id = state.get("product_id")
        run_id = state.get("run_id")
        if not product_id or not run_id:
            return None
        user_id = str(state.get("user_id", "local-development"))
        run_id = str(run_id)
        product_id = str(product_id)
        snapshot = self._product_snapshot(state)
        evidence_artifact_id = state.get("evidence_artifact_id") or (
            snapshot.evidence_artifact_id if snapshot is not None else None
        )
        mapping_artifact_id = (
            state.get("reviewed_mapping_artifact_id")
            or state.get("semantic_mapping_artifact_id")
            or (
                snapshot.reviewed_mapping_artifact_id or snapshot.semantic_mapping_artifact_id
                if snapshot is not None
                else None
            )
        )
        package = self._load_optional(
            evidence_artifact_id,
            ProductKnowledgePackage,
            user_id=user_id,
            product_id=product_id,
        )
        mapping = self._load_optional(
            mapping_artifact_id,
            MappingResult,
            user_id=user_id,
            product_id=product_id,
        )
        job = self._background_job(state)
        if job is not None:
            research_evidence_id = job.metadata.get("researchEvidenceArtifactId")
            research_package = self._load_optional(
                research_evidence_id,
                ProductKnowledgePackage,
                user_id=user_id,
                product_id=product_id,
            )
            if research_package is not None:
                if package is None:
                    package = research_package
                else:
                    sources = {item.id: item for item in package.acquired_sources}
                    sources.update(
                        {item.id: item for item in research_package.acquired_sources}
                    )
                    evidence = {item.id: item for item in package.evidence}
                    for item in research_package.evidence:
                        evidence.setdefault(item.id, item)
                    package = package.model_copy(
                        update={
                            "source_artifact_ids": tuple(
                                dict.fromkeys(
                                    (
                                        *package.source_artifact_ids,
                                        *research_package.source_artifact_ids,
                                    )
                                )
                            ),
                            "acquired_sources": tuple(sources.values()),
                            "extracted_pages": tuple(
                                {
                                    (item.source_url, index): item
                                    for index, item in enumerate(
                                        (
                                            *package.extracted_pages,
                                            *research_package.extracted_pages,
                                        )
                                    )
                                }.values()
                            ),
                            "evidence": tuple(evidence.values()),
                        }
                    )
            research_mapping_id = job.metadata.get("integratedMappingArtifactId")
            research_mapping = self._load_optional(
                research_mapping_id, MappingResult, user_id=user_id, product_id=product_id
            )
            if research_mapping is not None:
                mapping = merge_mapping_results(mapping or MappingResult(), research_mapping)
        index = self._load_optional(
            state.get("targets_artifact_id"), TemplateIndex, user_id=user_id, product_id=product_id
        )
        dpp = self._load_optional(
            state.get("dpp_artifact_id") or (snapshot.dpp_artifact_id if snapshot else None),
            DppPackage,
            user_id=user_id,
            product_id=product_id,
        )
        artifacts = self._context.catalogue.list_artifacts(
            run_id=run_id,
            user_id=user_id,
        )
        review_artifact_id = state.get("review_items_artifact_id")
        if not review_artifact_id and snapshot is not None and snapshot.human_review_pending:
            review_artifact_id = next(
                (
                    item.id
                    for item in reversed(artifacts)
                    if item.key
                    in {"mapping/review-items.json", "mapping/review-items-research.json"}
                ),
                None,
            )
        return ProductWork(
            product_id=str(product_id),
            status=(
                ProductStatus.AWAITING_REVIEW
                if snapshot is not None and snapshot.human_review_pending
                else self._product_status(str(state.get("status", "running")))
            ),
            product_name=(package.product_name if package else state.get("product_name")),
            source_urls=tuple(
                dict.fromkeys(
                    (
                        *state.get("known_source_urls", ()),
                        *((item.final_url for item in package.acquired_sources) if package else ()),
                    )
                )
            ),
            source_artifact_ids=package.source_artifact_ids if package else (),
            acquired_sources=package.acquired_sources if package else (),
            evidence=package.evidence if package else (),
            mapping_result=mapping,
            template_index=(
                index
                or self._load_optional(
                    snapshot.targets_artifact_id if snapshot is not None else None,
                    TemplateIndex,
                    user_id=user_id,
                    product_id=product_id,
                )
            ),
            pending_reviews=self._review_items(
                review_artifact_id,
                user_id=user_id,
                product_id=product_id,
            ),
            mapping_cycle_id=(
                state.get("mapping_cycle_id")
                or (snapshot.mapping_cycle_id if snapshot is not None else None)
            ),
            aas_artifact_sha256=dpp.artifact_sha256 if dpp else None,
            artifact_ids=tuple(item.id for item in artifacts),
        )

    def _product_snapshot(self, state: dict[str, Any]):
        product_id = state.get("product_id")
        run_id = state.get("run_id")
        if not product_id or not run_id:
            return None
        snapshot = self._context.catalogue.get_product_work_snapshot(
            str(product_id), user_id=str(state.get("user_id", "local-development"))
        )
        return snapshot if snapshot is not None and snapshot.run_id == str(run_id) else None

    def _background_job(self, state: dict[str, Any]):
        thread_id = str(state.get("thread_id", ""))
        run_id = str(state.get("run_id", ""))
        product_id = str(state.get("product_id", ""))
        user_id = str(state.get("user_id", "local-development"))
        if not thread_id or not run_id or not product_id:
            return None
        requested_id = state.get("background_job_id")
        jobs = self._context.catalogue.list_background_jobs(
            user_id=user_id, thread_id=thread_id
        )
        matching = tuple(
            job for job in jobs if job.run_id == run_id and job.product_id == product_id
        )
        if requested_id:
            selected = next((job for job in matching if job.id == requested_id), None)
            if selected is not None:
                return selected
        return matching[-1] if matching else None

    def _review_items(
        self, artifact_id: object, *, user_id: str, product_id: str
    ) -> tuple[SemanticReviewItem, ...]:
        if not artifact_id:
            return ()
        artifact = self._context.catalogue.get_artifact(
            str(artifact_id),
            user_id=user_id,
        )
        if artifact is None or artifact.product_id != product_id:
            return ()
        return tuple(
            SemanticReviewItem.model_validate(item)
            for item in json.loads(self._context.artifacts.get(artifact))
        )

    def _load_optional(
        self,
        artifact_id: object,
        model: type[ModelT],
        *,
        user_id: str,
        product_id: str,
    ) -> ModelT | None:
        if not artifact_id:
            return None
        try:
            artifact = self._context.catalogue.get_artifact(str(artifact_id), user_id=user_id)
            # Continue-saved-work intentionally reuses artifacts from earlier runs of
            # this same user/product. The user-scoped catalogue lookup prevents leakage;
            # product identity prevents accidentally loading another product's bytes.
            if artifact is None or artifact.product_id != product_id:
                return None
            return model.model_validate_json(self._context.artifacts.get(artifact))
        except (KeyError, ValueError):
            return None

    @staticmethod
    def _product_status(status: str) -> ProductStatus:
        return {
            "completed": ProductStatus.COMPLETED,
            "failed": ProductStatus.FAILED,
            "awaiting_review": ProductStatus.AWAITING_REVIEW,
        }.get(status, ProductStatus.IN_PROGRESS)

    @staticmethod
    def _pending_request(state: dict[str, Any]) -> HumanRequest | None:
        product_id = state.get("product_id")
        if not product_id:
            return None
        if state.get("review_required"):
            return HumanRequest(
                kind=HumanRequestKind.MAPPING_REVIEW,
                product_id=str(product_id),
                summary="The complete source-derived mapping needs one human confirmation.",
            )
        if state.get("required_unresolved", 0) and state.get("research_attempts", 0) >= state.get(
            "max_research_attempts", 2
        ):
            missing = tuple(state.get("missing_requirement_ids", ()))
            return HumanRequest(
                kind=HumanRequestKind.REQUIREMENT_VALUE,
                product_id=str(product_id),
                requirement_id=missing[0] if missing else None,
                summary="A mandatory value is still missing after public-source research.",
            )
        return None

    @staticmethod
    def _status(state: dict[str, Any], pending: HumanRequest | None) -> AgentStatus:
        if pending is not None:
            return (
                AgentStatus.AWAITING_REVIEW
                if pending.kind is HumanRequestKind.MAPPING_REVIEW
                else AgentStatus.AWAITING_INPUT
            )
        raw = str(state.get("status", AgentStatus.RUNNING.value))
        if raw in {item.value for item in AgentStatus}:
            return AgentStatus(raw)
        return AgentStatus.COMPLETED if raw == "reused" else AgentStatus.RUNNING

    @staticmethod
    def _decision_summary(status: AgentStatus) -> str:
        return {
            AgentStatus.AWAITING_COMPANY: "Company identity requires user selection.",
            AgentStatus.AWAITING_PRODUCT: "Product identity requires user selection.",
            AgentStatus.AWAITING_REVIEW: "Source-derived mappings require trusted review.",
            AgentStatus.AWAITING_INPUT: "Trusted human input is required.",
            AgentStatus.COMPLETED: "DPP workflow completed.",
            AgentStatus.FAILED: "DPP workflow failed.",
        }.get(status, "DPP workflow is running.")

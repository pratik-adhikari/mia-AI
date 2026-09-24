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
from mia_dpp.workflow.context import MiaContext
from mia_dpp.workflow.workspace import RunWorkspace

ModelT = TypeVar("ModelT", bound=BaseModel)


class AgentResponseView:
    """Translate graph state + durable artifacts into the legacy workspace contract."""

    def __init__(self, context: MiaContext, workspace: WorkspaceView) -> None:
        self._context = context
        self._workspace = workspace

    def build(self, state: dict[str, Any], *, trace_offset: int = 0) -> AgentResponse:
        thread_id = str(state.get("thread_id", ""))
        user_id = str(state.get("user_id", "local-development"))
        pending = self._pending_request(state)
        status = self._status(state, pending)
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
            current_product=self._current_product(state),
            pending_human_request=pending,
            trace_events=self._workspace.list_events(
                thread_id,
                trace_offset,
                user_id=user_id,
            ),
            artifact_count=len(self._workspace.list_artifacts(thread_id, user_id=user_id)),
            background_job_id=state.get("background_job_id") or None,
        )

    def _current_product(self, state: dict[str, Any]) -> ProductWork | None:
        product_id = state.get("product_id")
        run_id = state.get("run_id")
        if not product_id or not run_id:
            return None
        work = RunWorkspace(state, self._context)  # type: ignore[arg-type]
        package = self._load_optional(
            work, state.get("evidence_artifact_id"), ProductKnowledgePackage
        )
        mapping = self._load_optional(
            work,
            state.get("reviewed_mapping_artifact_id") or state.get("semantic_mapping_artifact_id"),
            MappingResult,
        )
        index = self._load_optional(work, state.get("targets_artifact_id"), TemplateIndex)
        dpp = self._load_optional(work, state.get("dpp_artifact_id"), DppPackage)
        artifacts = self._context.catalogue.list_artifacts(
            run_id=str(run_id),
            user_id=str(state.get("user_id", "local-development")),
        )
        return ProductWork(
            product_id=str(product_id),
            status=self._product_status(str(state.get("status", "running"))),
            product_name=(package.product_name if package else state.get("product_name")),
            source_urls=tuple(state.get("known_source_urls", ())),
            source_artifact_ids=package.source_artifact_ids if package else (),
            acquired_sources=package.acquired_sources if package else (),
            evidence=package.evidence if package else (),
            mapping_result=mapping,
            template_index=index,
            pending_reviews=self._review_items(work, state.get("review_items_artifact_id")),
            mapping_cycle_id=state.get("mapping_cycle_id") or None,
            aas_artifact_sha256=dpp.artifact_sha256 if dpp else None,
            artifact_ids=tuple(item.id for item in artifacts),
        )

    def _review_items(
        self,
        work: RunWorkspace,
        artifact_id: object,
    ) -> tuple[SemanticReviewItem, ...]:
        if not artifact_id:
            return ()
        artifact = self._context.catalogue.get_artifact(
            str(artifact_id),
            user_id=str(state.get("user_id") or "local-development"),
        )
        if artifact is None:
            return ()
        return tuple(
            SemanticReviewItem.model_validate(item)
            for item in json.loads(self._context.artifacts.get(artifact))
        )

    @staticmethod
    def _load_optional(
        work: RunWorkspace,
        artifact_id: object,
        model: type[ModelT],
    ) -> ModelT | None:
        if not artifact_id:
            return None
        try:
            return work.load(str(artifact_id), model)
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

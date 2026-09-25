"""Durable product-level workflow state and immutable human audit records."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field

from mia_dpp.domain.base import WireModel, utc_now
from mia_dpp.domain.mappings import ListInstanceBinding
from mia_dpp.domain.product import DppReleaseStatus


class ReuseMode(StrEnum):
    """How a product request should enter the workflow."""

    FRESH = "fresh"
    RESUME_CHECKPOINT = "resume_checkpoint"
    CONTINUE_SAVED_WORK = "continue_saved_work"
    REUSE_COMPLETED_DPP = "reuse_completed_dpp"
    REFRESH_SOURCES = "refresh_sources"


class ProductWorkStage(StrEnum):
    """Highest durable stage represented by a product snapshot."""

    RESOLVED = "resolved"
    EVIDENCE = "evidence"
    TARGETS = "targets"
    MAPPING = "mapping"
    HUMAN_REVIEW = "human_review"
    COVERAGE = "coverage"
    HUMAN_INPUT = "human_input"
    BUILD = "build"
    VALIDATION = "validation"
    COMPLETED = "completed"
    FAILED = "failed"


class ProductWorkSnapshot(WireModel):
    """Authoritative product-level pointers independent of any one chat checkpoint."""

    id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    workflow_stage: ProductWorkStage = ProductWorkStage.RESOLVED
    source_generation: int = Field(default=0, ge=0)

    template_keys: tuple[str, ...] = ()
    template_releases: tuple[str, ...] = ()

    evidence_artifact_id: str | None = None
    targets_artifact_id: str | None = None
    deterministic_mapping_artifact_id: str | None = None
    semantic_mapping_artifact_id: str | None = None
    semantic_promotion_artifact_id: str | None = None
    reviewed_mapping_artifact_id: str | None = None
    coverage_artifact_id: str | None = None
    conflict_artifact_id: str | None = None
    dpp_artifact_id: str | None = None
    aas_artifact_id: str | None = None
    validation_artifact_id: str | None = None
    release_status: DppReleaseStatus | None = None
    dummy_mapping_ids: tuple[str, ...] = ()

    source_fingerprint: str | None = None
    evidence_fingerprint: str | None = None
    target_fingerprint: str | None = None
    mapping_input_fingerprint: str | None = None
    semantic_mapper_fingerprint: str | None = None
    review_fingerprint: str | None = None
    reviewed_evidence_fingerprint: str | None = None
    reviewed_target_fingerprint: str | None = None
    reviewed_mapping_input_fingerprint: str | None = None
    build_input_fingerprint: str | None = None

    mapping_cycle_id: str | None = None
    unresolved_required_ids: tuple[str, ...] = ()
    conflicting_requirement_ids: tuple[str, ...] = ()
    human_review_pending: bool = False
    last_integrated_research_job_id: str | None = None
    last_error: str | None = None

    created_at: AwareDatetime = Field(default_factory=utc_now)
    updated_at: AwareDatetime = Field(default_factory=utc_now)


class HumanReviewAction(StrEnum):
    ACCEPTED_MAPPING = "accepted_mapping"
    CORRECTED_TARGET = "corrected_target"
    CORRECTED_VALUE = "corrected_value"
    SUPPLIED_VALUE = "supplied_value"
    SUPPLIED_DUMMY = "supplied_dummy"
    REJECTED_EVIDENCE = "rejected_evidence"
    MARKED_IRRELEVANT = "marked_irrelevant"
    MARKED_UNMAPPED = "marked_unmapped"
    RECONFIRMED_MAPPING = "reconfirmed_mapping"


class HumanReviewRecord(WireModel):
    """Immutable statement of what a named human changed or confirmed."""

    id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    mapping_cycle_id: str | None = None
    review_id: str | None = None
    evidence_id: str | None = None
    proposed_mapping_id: str | None = None
    proposed_requirement_id: str | None = None
    final_mapping_id: str | None = None
    final_requirement_id: str | None = None
    corrected_evidence_id: str | None = None
    proposed_value: str | None = None
    final_value: str | None = None
    proposed_target_path: tuple[str, ...] = ()
    final_target_path: tuple[str, ...] = ()
    proposed_semantic_id: str | None = None
    final_semantic_id: str | None = None
    proposed_list_instance_bindings: tuple[ListInstanceBinding, ...] = ()
    final_list_instance_bindings: tuple[ListInstanceBinding, ...] = ()
    action: HumanReviewAction
    actor_name: str | None = Field(default=None, max_length=200)
    comment: str | None = Field(default=None, max_length=1000)
    value_kind: Literal["verified", "dummy"] | None = None
    created_at: AwareDatetime = Field(default_factory=utc_now)


class ReuseDecision(WireModel):
    """Backend-owned decision about which durable product state may be reused."""

    mode: ReuseMode
    reason: str = Field(min_length=1)
    seeded_from_run_id: str | None = None
    evidence_artifact_id: str | None = None
    reviewed_mapping_artifact_id: str | None = None
    reused_dpp_version_id: str | None = None
    pending_research_job_id: str | None = None

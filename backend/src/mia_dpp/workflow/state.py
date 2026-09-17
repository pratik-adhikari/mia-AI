"""Small checkpointed LangGraph state for one product workflow."""

from __future__ import annotations

from typing import TypedDict


class MiaWorkflowState(TypedDict, total=False):
    # Conversation / identity
    thread_id: str
    user_message: str
    product_url: str
    product_id: str
    run_id: str
    refresh_requested: bool
    target_submodels: tuple[str, ...]

    # Cache and presentation
    cache_hit: bool
    reused_dpp_version_id: str
    product_name: str
    manufacturer: str
    image_url: str
    known_source_urls: tuple[str, ...]

    # Durable stage artifacts (large payloads never live in checkpoints)
    evidence_artifact_id: str
    targets_artifact_id: str
    deterministic_mapping_artifact_id: str
    semantic_mapping_artifact_id: str
    review_items_artifact_id: str
    reviewed_mapping_artifact_id: str
    coverage_artifact_id: str
    dpp_artifact_id: str
    aas_artifact_id: str
    validation_artifact_id: str

    # Routing summaries
    mapping_cycle_id: str
    review_required: bool
    required_unresolved: int
    missing_requirement_ids: tuple[str, ...]
    research_attempts: int
    research_found_source: bool
    max_research_attempts: int
    source_fingerprint: str
    build_deployable: bool

    # Terminal communication
    status: str
    reply: str
    decision_summary: str

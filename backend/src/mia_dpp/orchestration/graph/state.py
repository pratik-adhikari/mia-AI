"""Small checkpointed LangGraph state for one product workflow."""

from __future__ import annotations

from typing import TypedDict

from mia_dpp.domain.discovery import CompanyCandidate, ProductCandidate


class MiaWorkflowState(TypedDict, total=False):
    # Conversation / identity
    thread_id: str
    user_id: str
    user_message: str
    discovery_history_json: str
    company_candidates: tuple[CompanyCandidate, ...]
    selected_company: CompanyCandidate | None
    product_candidates: tuple[ProductCandidate, ...]
    selected_product_ids: tuple[str, ...]
    product_queue: tuple[str, ...]
    product_url: str
    product_id: str
    run_id: str
    workflow_generation: int
    source_generation: int
    refresh_requested: bool
    reuse_mode: str
    target_submodels: tuple[str, ...]

    # Cache and presentation
    product_snapshot_version: int
    cache_hit: bool
    reuse_prior_work: bool
    seeded_from_run_id: str
    reused_dpp_version_id: str
    product_name: str
    manufacturer: str
    image_url: str
    known_source_urls: tuple[str, ...]

    # Durable stage artifacts (large payloads never live in checkpoints)
    evidence_artifact_id: str
    normalization_artifact_id: str
    semantic_context_artifact_id: str
    jev_idta_routing_artifact_id: str
    jev_routing_diagnostics_artifact_id: str
    jev_decision_policy_artifact_id: str
    jev_semantic_grouping_artifact_id: str
    eclass_resolution_artifact_id: str
    eclass_diagnostics_artifact_id: str
    eclass_policy_artifact_id: str
    open_property_proposals_artifact_id: str
    targets_artifact_id: str
    deterministic_mapping_artifact_id: str
    semantic_mapping_artifact_id: str
    semantic_promotion_artifact_id: str
    review_items_artifact_id: str
    conflict_artifact_id: str
    reviewed_mapping_artifact_id: str
    coverage_artifact_id: str
    dpp_artifact_id: str
    aas_artifact_id: str
    validation_artifact_id: str
    background_job_id: str
    integrated_research_iteration: int

    # Routing summaries
    mapping_cycle_id: str
    review_required: bool
    required_unresolved: int
    missing_requirement_ids: tuple[str, ...]
    conflicting_requirement_ids: tuple[str, ...]
    research_attempts: int
    research_found_source: bool
    max_research_attempts: int
    source_fingerprint: str
    evidence_fingerprint: str
    target_fingerprint: str
    mapping_input_fingerprint: str
    semantic_mapper_fingerprint: str
    review_fingerprint: str
    build_input_fingerprint: str
    build_deployable: bool

    # Terminal communication
    status: str
    reply: str
    decision_summary: str


def reset_product_state(
    *, product_url: str = "", product_queue: tuple[str, ...] = ()
) -> dict[str, object]:
    """Return the run-scoped reset used when starting another product in a thread."""

    return {
        "product_url": product_url,
        "product_queue": product_queue,
        "product_id": "",
        "run_id": "",
        "workflow_generation": 0,
        "source_generation": 0,
        "cache_hit": False,
        "reuse_mode": "fresh",
        "product_snapshot_version": 0,
        "reuse_prior_work": False,
        "seeded_from_run_id": "",
        "reused_dpp_version_id": "",
        "product_name": "",
        "manufacturer": "",
        "image_url": "",
        "known_source_urls": (),
        "evidence_artifact_id": "",
        "normalization_artifact_id": "",
        "semantic_context_artifact_id": "",
        "jev_idta_routing_artifact_id": "",
        "jev_routing_diagnostics_artifact_id": "",
        "jev_decision_policy_artifact_id": "",
        "jev_semantic_grouping_artifact_id": "",
        "eclass_resolution_artifact_id": "",
        "eclass_diagnostics_artifact_id": "",
        "eclass_policy_artifact_id": "",
        "open_property_proposals_artifact_id": "",
        "targets_artifact_id": "",
        "deterministic_mapping_artifact_id": "",
        "semantic_mapping_artifact_id": "",
        "semantic_promotion_artifact_id": "",
        "review_items_artifact_id": "",
        "conflict_artifact_id": "",
        "reviewed_mapping_artifact_id": "",
        "coverage_artifact_id": "",
        "dpp_artifact_id": "",
        "aas_artifact_id": "",
        "validation_artifact_id": "",
        "background_job_id": "",
        "integrated_research_iteration": 0,
        "mapping_cycle_id": "",
        "review_required": False,
        "required_unresolved": 0,
        "missing_requirement_ids": (),
        "conflicting_requirement_ids": (),
        "research_attempts": 0,
        "research_found_source": False,
        "source_fingerprint": "",
        "evidence_fingerprint": "",
        "target_fingerprint": "",
        "mapping_input_fingerprint": "",
        "semantic_mapper_fingerprint": "",
        "review_fingerprint": "",
        "build_input_fingerprint": "",
        "build_deployable": False,
        "status": "running",
    }

"""Pure routing decisions for the DPP workflow."""

from __future__ import annotations

from typing import Literal

from mia_dpp.workflow.state import MiaWorkflowState


def after_product_lookup(state: MiaWorkflowState) -> Literal["reuse", "extract"]:
    return "reuse" if state.get("cache_hit", False) else "extract"


def after_semantic_mapping(state: MiaWorkflowState) -> Literal["review", "coverage"]:
    return "review" if state.get("review_required", False) else "coverage"


def after_coverage(state: MiaWorkflowState) -> Literal["build", "research", "human"]:
    if state.get("required_unresolved", 0) == 0:
        return "build"
    if state.get("research_attempts", 0) < state.get("max_research_attempts", 2):
        return "research"
    return "human"


def after_research(state: MiaWorkflowState) -> Literal["extract", "human"]:
    return "extract" if state.get("research_found_source", False) else "human"


PRODUCT_ROUTES = {"reuse": "reuse_existing_dpp", "extract": "extract_evidence"}
SEMANTIC_ROUTES = {"review": "human_review", "coverage": "coverage"}
COVERAGE_ROUTES = {"build": "build_aas", "research": "research", "human": "human_value"}
RESEARCH_ROUTES = {"extract": "extract_evidence", "human": "human_value"}

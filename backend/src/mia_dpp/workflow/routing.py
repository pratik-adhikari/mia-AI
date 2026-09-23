"""Pure routing decisions for the DPP workflow."""

from __future__ import annotations

from collections.abc import Hashable
from typing import Literal

from mia_dpp.workflow.state import MiaWorkflowState


def after_discovery(state: MiaWorkflowState) -> Literal["process", "wait"]:
    return "process" if state.get("product_url") and state.get("status") == "running" else "wait"


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


def after_product_done(state: MiaWorkflowState) -> Literal["next", "done"]:
    return "next" if state.get("product_queue") else "done"


DISCOVERY_ROUTES: dict[Hashable, str] = {"process": "resolve_product", "wait": "__end__"}
PRODUCT_ROUTES: dict[Hashable, str] = {
    "reuse": "reuse_existing_dpp",
    "extract": "evidence_and_coverage",
}
SEMANTIC_ROUTES: dict[Hashable, str] = {
    "review": "human_review",
    "coverage": "integrate_background_research",
}
PREPARATION_ROUTES: dict[Hashable, str] = {
    "build": "__end__",
    "research": "research",
    "human": "human_value",
}
RESEARCH_ROUTES: dict[Hashable, str] = {"extract": "extract_evidence", "human": "human_value"}
DONE_ROUTES: dict[Hashable, str] = {"next": "advance_product", "done": "__end__"}

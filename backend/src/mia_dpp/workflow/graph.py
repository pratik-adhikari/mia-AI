"""LangGraph wiring only; business logic lives in nodes and existing MIA services."""

from __future__ import annotations

from typing import Any

from langgraph.graph import START, StateGraph

from mia_dpp.workflow.context import MiaContext
from mia_dpp.workflow.nodes_aas import build_aas, store_result
from mia_dpp.workflow.nodes_discovery import discover_product
from mia_dpp.workflow.nodes_mapping import (
    build_targets,
    coverage,
    deterministic_mapping,
    human_review,
    human_value,
    semantic_mapping,
)
from mia_dpp.workflow.nodes_product import (
    advance_product,
    extract_evidence,
    resolve_product,
    reuse_existing_dpp,
)
from mia_dpp.workflow.nodes_research import research
from mia_dpp.workflow.routing import (
    COVERAGE_ROUTES,
    DISCOVERY_ROUTES,
    DONE_ROUTES,
    PRODUCT_ROUTES,
    RESEARCH_ROUTES,
    SEMANTIC_ROUTES,
    after_coverage,
    after_discovery,
    after_product_done,
    after_product_lookup,
    after_research,
    after_semantic_mapping,
)
from mia_dpp.workflow.state import MiaWorkflowState


def create_graph(checkpointer: Any) -> Any:
    graph = StateGraph(MiaWorkflowState, context_schema=MiaContext)
    for node in (
        discover_product,
        resolve_product,
        reuse_existing_dpp,
        extract_evidence,
        build_targets,
        deterministic_mapping,
        semantic_mapping,
        human_review,
        coverage,
        research,
        human_value,
        build_aas,
        store_result,
        advance_product,
    ):
        graph.add_node(node)

    graph.add_edge(START, "discover_product")
    graph.add_conditional_edges("discover_product", after_discovery, DISCOVERY_ROUTES)
    graph.add_conditional_edges("resolve_product", after_product_lookup, PRODUCT_ROUTES)
    graph.add_edge("extract_evidence", "build_targets")
    graph.add_edge("build_targets", "deterministic_mapping")
    graph.add_edge("deterministic_mapping", "semantic_mapping")
    graph.add_conditional_edges("semantic_mapping", after_semantic_mapping, SEMANTIC_ROUTES)
    graph.add_edge("human_review", "coverage")
    graph.add_conditional_edges("coverage", after_coverage, COVERAGE_ROUTES)
    graph.add_conditional_edges("research", after_research, RESEARCH_ROUTES)
    graph.add_edge("human_value", "coverage")
    graph.add_edge("build_aas", "store_result")
    graph.add_conditional_edges("reuse_existing_dpp", after_product_done, DONE_ROUTES)
    graph.add_conditional_edges("store_result", after_product_done, DONE_ROUTES)
    graph.add_edge("advance_product", "resolve_product")
    return graph.compile(checkpointer=checkpointer)

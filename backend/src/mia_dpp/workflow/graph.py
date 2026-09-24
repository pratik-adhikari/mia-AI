"""LangGraph wiring only; business logic lives in nodes and existing MIA services."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from mia_dpp.workflow.context import MiaContext
from mia_dpp.workflow.nodes_aas import build_aas, store_result
from mia_dpp.workflow.nodes_discovery import discover_product
from mia_dpp.workflow.nodes_mapping import (
    build_targets,
    coverage,
    deterministic_mapping,
    human_review,
    human_value,
    integrate_background_research,
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
    BACKGROUND_INTEGRATION_ROUTES,
    DISCOVERY_ROUTES,
    DONE_ROUTES,
    PREPARATION_ROUTES,
    PRODUCT_ROUTES,
    RESEARCH_ROUTES,
    SEMANTIC_ROUTES,
    after_background_integration,
    after_coverage,
    after_discovery,
    after_product_done,
    after_product_lookup,
    after_research,
    after_semantic_mapping,
)
from mia_dpp.workflow.state import MiaWorkflowState


def _bind_context(node: Any, context: MiaContext) -> Any:
    async def run(state: MiaWorkflowState) -> Any:
        return await node(state, Runtime(context=context))

    run.__name__ = node.__name__
    return run


def _add_nodes(builder: Any, nodes: tuple[Any, ...], context: MiaContext | None) -> None:
    for node in nodes:
        implementation = (
            _bind_context(node, context)
            if context is not None and node is not advance_product
            else node
        )
        builder.add_node(node.__name__, implementation)


def _build_evidence_stage(context: MiaContext | None) -> Any:
    stage = StateGraph(
        MiaWorkflowState,
        context_schema=MiaContext if context is None else None,
    )
    _add_nodes(
        stage,
        (
            extract_evidence,
            build_targets,
            deterministic_mapping,
            semantic_mapping,
            human_review,
            integrate_background_research,
            coverage,
            research,
            human_value,
        ),
        context,
    )
    stage.add_edge(START, "extract_evidence")
    stage.add_edge("extract_evidence", "build_targets")
    stage.add_edge("build_targets", "deterministic_mapping")
    stage.add_edge("deterministic_mapping", "semantic_mapping")
    stage.add_conditional_edges("semantic_mapping", after_semantic_mapping, SEMANTIC_ROUTES)
    stage.add_edge("human_review", "integrate_background_research")
    stage.add_conditional_edges(
        "integrate_background_research",
        after_background_integration,
        BACKGROUND_INTEGRATION_ROUTES,
    )
    stage.add_conditional_edges("coverage", after_coverage, PREPARATION_ROUTES)
    stage.add_conditional_edges("research", after_research, RESEARCH_ROUTES)
    stage.add_edge("human_value", "coverage")
    return stage.compile()


def _build_aas_stage(context: MiaContext | None) -> Any:
    stage = StateGraph(
        MiaWorkflowState,
        context_schema=MiaContext if context is None else None,
    )
    _add_nodes(stage, (build_aas, store_result), context)
    stage.add_edge(START, "build_aas")
    stage.add_edge("build_aas", "store_result")
    stage.add_edge("store_result", END)
    return stage.compile()


def create_graph(
    checkpointer: Any | None = None,
    *,
    context: MiaContext | None = None,
) -> Any:
    graph = StateGraph(
        MiaWorkflowState,
        context_schema=MiaContext if context is None else None,
    )
    _add_nodes(
        graph,
        (discover_product, resolve_product, reuse_existing_dpp, advance_product),
        context,
    )
    graph.add_node("evidence_and_coverage", _build_evidence_stage(context))
    graph.add_node("aas_output", _build_aas_stage(context))

    graph.add_edge(START, "discover_product")
    graph.add_conditional_edges("discover_product", after_discovery, DISCOVERY_ROUTES)
    graph.add_conditional_edges("resolve_product", after_product_lookup, PRODUCT_ROUTES)
    graph.add_edge("evidence_and_coverage", "aas_output")
    graph.add_conditional_edges("reuse_existing_dpp", after_product_done, DONE_ROUTES)
    graph.add_conditional_edges("aas_output", after_product_done, DONE_ROUTES)
    graph.add_edge("advance_product", "resolve_product")
    return graph.compile(checkpointer=checkpointer)

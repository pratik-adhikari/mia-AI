"""Shadow semantic-preparation nodes for normalization and context construction."""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.normalization import NormalizationReport, normalize_package
from mia_dpp.semantic import ContextViewSet, build_context_views
from mia_dpp.semantic.idta_routing import route_views
from mia_dpp.workflow.context import MiaContext
from mia_dpp.workflow.state import MiaWorkflowState
from mia_dpp.workflow.workspace import RunWorkspace


async def normalize_evidence(
    state: MiaWorkflowState,
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    """Build a lossless derived normalization artifact for every evidence record."""

    work = RunWorkspace(state, runtime.context)
    package = work.load_state("evidence_artifact_id", ProductKnowledgePackage)
    report = normalize_package(package)
    artifact_id = work.put_model(
        "semantic/normalization.json",
        report,
        derived_from=(work.state_id("evidence_artifact_id"),),
    )
    ambiguous = sum(item.status.value == "ambiguous" for item in report.evidence)
    work.event(
        "semantic.normalization_completed",
        f"Normalized {len(report.evidence)} evidence records; {ambiguous} remain syntactically ambiguous.",
        metadata={
            "artifactId": artifact_id,
            "normalizerVersion": report.version,
            "evidenceCount": len(report.evidence),
            "ambiguousCount": ambiguous,
            "shadowMode": True,
        },
    )
    return {"normalization_artifact_id": artifact_id}


async def build_semantic_context(
    state: MiaWorkflowState,
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    """Create deterministic multi-scope context views for future Jev strategies."""

    work = RunWorkspace(state, runtime.context)
    package = work.load_state("evidence_artifact_id", ProductKnowledgePackage)
    normalization = work.load_state("normalization_artifact_id", NormalizationReport)
    views = build_context_views(package, normalization)
    artifact_id = work.put_model(
        "semantic/context-views.json",
        views,
        derived_from=(
            work.state_id("evidence_artifact_id"),
            work.state_id("normalization_artifact_id"),
        ),
    )
    scope_counts: dict[str, int] = {}
    for view in views.views:
        scope_counts[view.scope.value] = scope_counts.get(view.scope.value, 0) + 1
    work.event(
        "semantic.context_views_completed",
        f"Built {len(views.views)} multi-scope context views for Jev shadow evaluation.",
        metadata={
            "artifactId": artifact_id,
            "viewCount": len(views.views),
            "scopeCounts": scope_counts,
            "shadowMode": True,
        },
    )
    return {"semantic_context_artifact_id": artifact_id}


async def shadow_jev_idta_routing(
    state: MiaWorkflowState,
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    """Run hierarchical Jev routing without changing trusted mapping output."""

    work = RunWorkspace(state, runtime.context)
    decider = work.ctx.jev_decider
    if decider is None:
        work.event(
            "semantic.jev_shadow_skipped",
            "Jev shadow routing is disabled; existing semantic mapping remains authoritative.",
            metadata={"shadowMode": True},
        )
        return {}

    package = work.load_state("evidence_artifact_id", ProductKnowledgePackage)
    normalization = work.load_state("normalization_artifact_id", NormalizationReport)
    context_views = work.load_state("semantic_context_artifact_id", ContextViewSet)
    template_keys = state.get(
        "target_submodels",
        ("digital_nameplate", "technical_data"),
    )
    report = await route_views(
        decider=decider,
        repository=work.ctx.templates,
        selected_template_keys=template_keys,
        package=package,
        normalization=normalization,
        context_views=context_views,
        scopes=work.ctx.jev_routing_scopes,
        max_concurrency=work.ctx.jev_routing_max_concurrency,
    )
    artifact_id = work.put_model(
        "semantic/jev-idta-routing-shadow.json",
        report,
        derived_from=(
            work.state_id("normalization_artifact_id"),
            work.state_id("semantic_context_artifact_id"),
        ),
    )
    terminal_counts: dict[str, int] = {}
    for trace in report.traces:
        terminal_counts[trace.terminal_reason] = (
            terminal_counts.get(trace.terminal_reason, 0) + 1
        )
    work.event(
        "semantic.jev_shadow_completed",
        f"Recorded {len(report.traces)} hierarchical Jev routing traces.",
        metadata={
            "artifactId": artifact_id,
            "traceCount": len(report.traces),
            "terminalCounts": terminal_counts,
            "scopes": [scope.value for scope in work.ctx.jev_routing_scopes],
            "shadowMode": True,
        },
    )
    return {"jev_idta_routing_artifact_id": artifact_id}

"""Shadow semantic-preparation nodes for normalization and context construction."""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.normalization import NormalizationReport, normalize_package
from mia_dpp.semantic import ContextViewSet, build_context_views
from mia_dpp.semantic.decision_policy import apply_decision_policy
from mia_dpp.semantic.diagnostics import build_routing_diagnostics
from mia_dpp.semantic.eclass_diagnostics import (
    build_eclass_diagnostics,
    eclass_policy_decisions,
)
from mia_dpp.semantic.eclass_resolution import (
    EclassResolutionReport,
    resolve_eclass_for_technical_properties,
)
from mia_dpp.semantic.grouping import build_grouping_report
from mia_dpp.semantic.idta_routing import IdtaRoutingReport, route_views
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
        (
            f"Normalized {len(report.evidence)} evidence records; "
            f"{ambiguous} remain syntactically ambiguous."
        ),
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


async def analyze_jev_shadow(
    state: MiaWorkflowState,
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    """Derive multi-scope diagnostics and review priority without model calls."""

    work = RunWorkspace(state, runtime.context)
    if not work.ctx.eclass_shadow_enabled:
        work.event(
            "semantic.eclass_shadow_skipped",
            "ECLASS shadow resolution is disabled.",
            metadata={"shadowMode": True},
        )
        return {}
    if work.ctx.eclass_provider is None:
        raise RuntimeError("ECLASS shadow resolution is enabled without a provider")

    routing_id = state.get("jev_idta_routing_artifact_id")
    if not routing_id:
        work.event(
            "semantic.jev_policy_skipped",
            "No Jev shadow routing artifact exists, so diagnostics were skipped.",
            metadata={"shadowMode": True},
        )
        return {}

    routing = work.load(routing_id, IdtaRoutingReport)
    diagnostics = build_routing_diagnostics(routing)
    diagnostics_id = work.put_model(
        "semantic/jev-routing-diagnostics.json",
        diagnostics,
        derived_from=(routing_id,),
    )
    policy = apply_decision_policy(
        diagnostics,
        work.ctx.jev_decision_policy,
    )
    policy_id = work.put_model(
        "semantic/jev-decision-policy.json",
        policy,
        derived_from=(diagnostics_id,),
    )
    counts: dict[str, int] = {}
    for decision in policy.decisions:
        counts[decision.priority.value] = counts.get(decision.priority.value, 0) + 1
    work.event(
        "semantic.jev_policy_completed",
        "Derived shadow human-attention priorities from saved Jev distributions.",
        metadata={
            "diagnosticsArtifactId": diagnostics_id,
            "policyArtifactId": policy_id,
            "priorityCounts": counts,
            "shadowMode": True,
        },
    )
    return {
        "jev_routing_diagnostics_artifact_id": diagnostics_id,
        "jev_decision_policy_artifact_id": policy_id,
    }


async def shadow_jev_semantic_grouping(
    state: MiaWorkflowState,
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    """Build lossless semantic grouping metadata without affecting trusted mappings."""

    work = RunWorkspace(state, runtime.context)
    package = work.load_state("evidence_artifact_id", ProductKnowledgePackage)
    normalization = work.load_state("normalization_artifact_id", NormalizationReport)
    context_views = work.load_state("semantic_context_artifact_id", ContextViewSet)
    report = await build_grouping_report(
        decider=work.ctx.jev_decider,
        package=package,
        normalization=normalization,
        context_views=context_views,
        scopes=work.ctx.jev_grouping_scopes,
        max_groups=work.ctx.jev_grouping_max_groups,
    )
    artifact_id = work.put_model(
        "semantic/jev-semantic-grouping-shadow.json",
        report,
        derived_from=(
            work.state_id("evidence_artifact_id"),
            work.state_id("normalization_artifact_id"),
            work.state_id("semantic_context_artifact_id"),
        ),
    )
    run_summaries = {
        (
            f"{run.strategy.value}:{run.scope.value}"
            if run.scope is not None
            else run.strategy.value
        ): {
            "groups": len(run.groups),
            "unresolved": sum(
                assignment.group_id is None for assignment in run.assignments
            ),
        }
        for run in report.runs
    }
    work.event(
        "semantic.grouping_shadow_completed",
        "Recorded lossless semantic grouping strategies without changing evidence.",
        metadata={
            "artifactId": artifact_id,
            "runs": run_summaries,
            "pairwiseComparisons": len(report.pairwise_agreement),
            "shadowMode": True,
        },
    )
    return {"jev_semantic_grouping_artifact_id": artifact_id}


async def shadow_eclass_resolution(
    state: MiaWorkflowState,
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    """Retrieve verified ECLASS properties for open Technical Properties only."""

    work = RunWorkspace(state, runtime.context)
    routing_id = state.get("jev_idta_routing_artifact_id")
    if not routing_id:
        work.event(
            "semantic.eclass_shadow_skipped",
            "No Jev IDTA routing artifact exists, so ECLASS resolution was skipped.",
            metadata={"shadowMode": True},
        )
        return {}

    package = work.load_state("evidence_artifact_id", ProductKnowledgePackage)
    normalization = work.load_state("normalization_artifact_id", NormalizationReport)
    context_views = work.load_state("semantic_context_artifact_id", ContextViewSet)
    routing = work.load(routing_id, IdtaRoutingReport)
    report: EclassResolutionReport = await resolve_eclass_for_technical_properties(
        provider=work.ctx.eclass_provider,
        decider=work.ctx.jev_decider,
        package=package,
        normalization=normalization,
        context_views=context_views,
        routing=routing,
        scopes=work.ctx.eclass_resolution_scopes,
        search_limit=work.ctx.eclass_candidate_limit,
        verification_concurrency=work.ctx.jev_routing_max_concurrency,
    )
    artifact_id = work.put_model(
        "semantic/eclass-resolution-shadow.json",
        report,
        derived_from=(
            routing_id,
            work.state_id("normalization_artifact_id"),
            work.state_id("semantic_context_artifact_id"),
        ),
    )
    status_counts: dict[str, int] = {}
    decision_count = 0
    for result in report.results:
        status_counts[result.retrieval_status] = (
            status_counts.get(result.retrieval_status, 0) + 1
        )
        decision_count += len(result.decisions)
    work.event(
        "semantic.eclass_shadow_completed",
        "Retrieved, verified, and classified ECLASS candidates in shadow mode.",
        metadata={
            "artifactId": artifact_id,
            "provider": report.provider_name,
            "statusCounts": status_counts,
            "jevDecisions": decision_count,
            "shadowMode": True,
        },
    )
    return {"eclass_resolution_artifact_id": artifact_id}


async def analyze_eclass_shadow(
    state: MiaWorkflowState,
    runtime: Runtime[MiaContext],
) -> dict[str, Any]:
    """Derive ECLASS cross-scope diagnostics and attention priority without model calls."""

    work = RunWorkspace(state, runtime.context)
    resolution_id = state.get("eclass_resolution_artifact_id")
    if not resolution_id:
        work.event(
            "semantic.eclass_policy_skipped",
            "No ECLASS resolution artifact exists, so ECLASS diagnostics were skipped.",
            metadata={"shadowMode": True},
        )
        return {}

    resolution = work.load(resolution_id, EclassResolutionReport)
    diagnostics = build_eclass_diagnostics(resolution)
    diagnostics_id = work.put_model(
        "semantic/eclass-resolution-diagnostics.json",
        diagnostics,
        derived_from=(resolution_id,),
    )
    decisions = eclass_policy_decisions(
        diagnostics,
        work.ctx.jev_decision_policy,
    )
    policy_id = work.put_json(
        "semantic/eclass-decision-policy.json",
        {
            "settings": work.ctx.jev_decision_policy.model_dump(
                mode="json",
                by_alias=True,
            ),
            "decisions": [
                item.model_dump(mode="json", by_alias=True)
                for item in decisions
            ],
        },
        derived_from=(diagnostics_id,),
    )
    priority_counts: dict[str, int] = {}
    for decision in decisions:
        priority_counts[decision.priority.value] = (
            priority_counts.get(decision.priority.value, 0) + 1
        )
    work.event(
        "semantic.eclass_policy_completed",
        "Derived shadow ECLASS cross-scope diagnostics and attention priorities.",
        metadata={
            "diagnosticsArtifactId": diagnostics_id,
            "policyArtifactId": policy_id,
            "priorityCounts": priority_counts,
            "shadowMode": True,
        },
    )
    return {
        "eclass_diagnostics_artifact_id": diagnostics_id,
        "eclass_policy_artifact_id": policy_id,
    }

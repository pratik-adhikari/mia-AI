"""Shadow semantic-preparation nodes for normalization and context construction."""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.normalization import NormalizationReport, normalize_package
from mia_dpp.semantic import ContextViewSet, build_context_views
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

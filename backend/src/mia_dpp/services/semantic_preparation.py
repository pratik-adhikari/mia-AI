"""Reusable semantic preparation over persisted product evidence."""

from __future__ import annotations

from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.normalization import NormalizationReport, normalize_package
from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.runtime.run_context import RunContext
from mia_dpp.runtime.run_store import RunStore
from mia_dpp.semantic import ContextViewSet, build_context_views
from mia_dpp.semantic.decision_policy import DecisionPolicySettings, apply_decision_policy
from mia_dpp.semantic.diagnostics import build_routing_diagnostics
from mia_dpp.semantic.eclass import EclassPropertyProvider
from mia_dpp.semantic.eclass_diagnostics import (
    EclassDiagnosticsReport,
    build_eclass_diagnostics,
    eclass_policy_decisions,
)
from mia_dpp.semantic.eclass_resolution import (
    EclassResolutionReport,
    resolve_eclass_for_technical_properties,
)
from mia_dpp.semantic.grouping import build_grouping_report
from mia_dpp.semantic.idta_routing import IdtaRoutingReport, route_views
from mia_dpp.semantic.jev import JevDecisionClient
from mia_dpp.semantic.models import ContextScope
from mia_dpp.semantic.open_property import (
    OpenPropertyProposalReport,
    build_open_property_proposals,
)
from mia_dpp.storage.base import ArtifactStore


class SemanticPreparationService:
    """Build normalization, semantic routing, grouping, and ECLASS artifacts."""

    def __init__(
        self,
        *,
        catalogue: ProductCatalogue,
        artifacts: ArtifactStore,
        templates: OfficialTemplateRepository,
        jev_decider: JevDecisionClient | None,
        jev_mapping_enabled: bool,
        jev_routing_scopes: tuple[ContextScope, ...],
        jev_routing_max_concurrency: int,
        jev_decision_policy: DecisionPolicySettings,
        jev_grouping_scopes: tuple[ContextScope, ...],
        jev_grouping_max_groups: int,
        eclass_shadow_enabled: bool,
        eclass_provider: EclassPropertyProvider | None,
        eclass_resolution_scopes: tuple[ContextScope, ...],
        eclass_candidate_limit: int,
    ) -> None:
        self._catalogue = catalogue
        self._artifacts = artifacts
        self._templates = templates
        self._jev_decider = jev_decider
        self._jev_mapping_enabled = jev_mapping_enabled
        self._jev_routing_scopes = jev_routing_scopes
        self._jev_routing_max_concurrency = jev_routing_max_concurrency
        self._jev_decision_policy = jev_decision_policy
        self._jev_grouping_scopes = jev_grouping_scopes
        self._jev_grouping_max_groups = jev_grouping_max_groups
        self._eclass_shadow_enabled = eclass_shadow_enabled
        self._eclass_provider = eclass_provider
        self._eclass_resolution_scopes = eclass_resolution_scopes
        self._eclass_candidate_limit = eclass_candidate_limit

    def _store(self, context: RunContext) -> RunStore:
        return RunStore(context, self._catalogue, self._artifacts)

    def normalize(self, context: RunContext, evidence_artifact_id: str) -> str:
        work = self._store(context)
        package = work.load(evidence_artifact_id, ProductKnowledgePackage)
        report = normalize_package(package)
        artifact_id = work.put_model(
            "semantic/normalization.json",
            report,
            derived_from=(evidence_artifact_id,),
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
                "shadowMode": not self._jev_mapping_enabled,
            },
        )
        return artifact_id

    def build_context(
        self,
        context: RunContext,
        evidence_artifact_id: str,
        normalization_artifact_id: str,
    ) -> str:
        work = self._store(context)
        package = work.load(evidence_artifact_id, ProductKnowledgePackage)
        normalization = work.load(normalization_artifact_id, NormalizationReport)
        views = build_context_views(package, normalization)
        artifact_id = work.put_model(
            "semantic/context-views.json",
            views,
            derived_from=(evidence_artifact_id, normalization_artifact_id),
        )
        scope_counts: dict[str, int] = {}
        for view in views.views:
            scope_counts[view.scope.value] = scope_counts.get(view.scope.value, 0) + 1
        work.event(
            "semantic.context_views_completed",
            f"Built {len(views.views)} multi-scope context views for Jev routing.",
            metadata={
                "artifactId": artifact_id,
                "viewCount": len(views.views),
                "scopeCounts": scope_counts,
                "shadowMode": not self._jev_mapping_enabled,
            },
        )
        return artifact_id

    async def route_jev(
        self,
        context: RunContext,
        *,
        evidence_artifact_id: str,
        normalization_artifact_id: str,
        semantic_context_artifact_id: str,
        template_keys: tuple[str, ...],
    ) -> str | None:
        work = self._store(context)
        if self._jev_decider is None:
            work.event(
                "semantic.jev_shadow_skipped",
                "Jev shadow routing is disabled; existing semantic mapping remains authoritative.",
                metadata={"shadowMode": True},
            )
            return None

        package = work.load(evidence_artifact_id, ProductKnowledgePackage)
        normalization = work.load(normalization_artifact_id, NormalizationReport)
        context_views = work.load(semantic_context_artifact_id, ContextViewSet)
        report = await route_views(
            decider=self._jev_decider,
            repository=self._templates,
            selected_template_keys=template_keys,
            package=package,
            normalization=normalization,
            context_views=context_views,
            scopes=self._jev_routing_scopes,
            max_concurrency=self._jev_routing_max_concurrency,
        )
        artifact_id = work.put_model(
            "semantic/jev-idta-routing-shadow.json",
            report,
            derived_from=(normalization_artifact_id, semantic_context_artifact_id),
        )
        terminal_counts: dict[str, int] = {}
        for trace in report.traces:
            terminal_counts[trace.terminal_reason] = terminal_counts.get(trace.terminal_reason, 0) + 1
        work.event(
            "semantic.jev_shadow_completed",
            f"Recorded {len(report.traces)} hierarchical Jev routing traces.",
            metadata={
                "artifactId": artifact_id,
                "traceCount": len(report.traces),
                "terminalCounts": terminal_counts,
                "scopes": [scope.value for scope in self._jev_routing_scopes],
                "shadowMode": not self._jev_mapping_enabled,
            },
        )
        return artifact_id

    def analyze_jev(self, context: RunContext, routing_artifact_id: str | None) -> tuple[str, str] | None:
        work = self._store(context)
        if not routing_artifact_id:
            work.event(
                "semantic.jev_policy_skipped",
                "No Jev shadow routing artifact exists, so diagnostics were skipped.",
                metadata={"shadowMode": True},
            )
            return None

        routing = work.load(routing_artifact_id, IdtaRoutingReport)
        diagnostics = build_routing_diagnostics(routing)
        diagnostics_id = work.put_model(
            "semantic/jev-routing-diagnostics.json",
            diagnostics,
            derived_from=(routing_artifact_id,),
        )
        policy = apply_decision_policy(diagnostics, self._jev_decision_policy)
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
            "Derived human-attention priorities from saved Jev distributions.",
            metadata={
                "diagnosticsArtifactId": diagnostics_id,
                "policyArtifactId": policy_id,
                "priorityCounts": counts,
                "shadowMode": not self._jev_mapping_enabled,
            },
        )
        return diagnostics_id, policy_id

    async def group_semantics(
        self,
        context: RunContext,
        *,
        evidence_artifact_id: str,
        normalization_artifact_id: str,
        semantic_context_artifact_id: str,
    ) -> str:
        work = self._store(context)
        package = work.load(evidence_artifact_id, ProductKnowledgePackage)
        normalization = work.load(normalization_artifact_id, NormalizationReport)
        context_views = work.load(semantic_context_artifact_id, ContextViewSet)
        report = await build_grouping_report(
            decider=self._jev_decider,
            package=package,
            normalization=normalization,
            context_views=context_views,
            scopes=self._jev_grouping_scopes,
            max_groups=self._jev_grouping_max_groups,
        )
        artifact_id = work.put_model(
            "semantic/jev-semantic-grouping-shadow.json",
            report,
            derived_from=(
                evidence_artifact_id,
                normalization_artifact_id,
                semantic_context_artifact_id,
            ),
        )
        run_summaries = {
            (
                f"{run.strategy.value}:{run.scope.value}"
                if run.scope is not None
                else run.strategy.value
            ): {
                "groups": len(run.groups),
                "unresolved": sum(assignment.group_id is None for assignment in run.assignments),
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
        return artifact_id

    async def resolve_eclass(
        self,
        context: RunContext,
        *,
        evidence_artifact_id: str,
        normalization_artifact_id: str,
        semantic_context_artifact_id: str,
        routing_artifact_id: str | None,
    ) -> str | None:
        work = self._store(context)
        if not self._eclass_shadow_enabled:
            work.event(
                "semantic.eclass_shadow_skipped",
                "ECLASS shadow resolution is disabled.",
                metadata={"shadowMode": True},
            )
            return None
        if self._eclass_provider is None:
            raise RuntimeError("ECLASS shadow resolution is enabled without a provider")
        if not routing_artifact_id:
            work.event(
                "semantic.eclass_shadow_skipped",
                "No Jev template-choice result exists, so ECLASS resolution was skipped.",
                metadata={"shadowMode": True},
            )
            return None

        package = work.load(evidence_artifact_id, ProductKnowledgePackage)
        normalization = work.load(normalization_artifact_id, NormalizationReport)
        context_views = work.load(semantic_context_artifact_id, ContextViewSet)
        routing = work.load(routing_artifact_id, IdtaRoutingReport)
        report: EclassResolutionReport = await resolve_eclass_for_technical_properties(
            provider=self._eclass_provider,
            decider=self._jev_decider,
            package=package,
            normalization=normalization,
            context_views=context_views,
            routing=routing,
            scopes=self._eclass_resolution_scopes,
            search_limit=self._eclass_candidate_limit,
            verification_concurrency=self._jev_routing_max_concurrency,
        )
        artifact_id = work.put_model(
            "semantic/eclass-resolution-shadow.json",
            report,
            derived_from=(
                routing_artifact_id,
                normalization_artifact_id,
                semantic_context_artifact_id,
            ),
        )
        status_counts: dict[str, int] = {}
        decision_count = 0
        for result in report.results:
            status_counts[result.retrieval_status] = status_counts.get(result.retrieval_status, 0) + 1
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
        return artifact_id

    def analyze_eclass(
        self,
        context: RunContext,
        resolution_artifact_id: str | None,
    ) -> tuple[str, str] | None:
        work = self._store(context)
        if not resolution_artifact_id:
            work.event(
                "semantic.eclass_policy_skipped",
                "No ECLASS resolution artifact exists, so ECLASS diagnostics were skipped.",
                metadata={"shadowMode": True},
            )
            return None

        resolution = work.load(resolution_artifact_id, EclassResolutionReport)
        diagnostics = build_eclass_diagnostics(resolution)
        diagnostics_id = work.put_model(
            "semantic/eclass-resolution-diagnostics.json",
            diagnostics,
            derived_from=(resolution_artifact_id,),
        )
        decisions = eclass_policy_decisions(diagnostics, self._jev_decision_policy)
        policy_id = work.put_json(
            "semantic/eclass-decision-policy.json",
            {
                "settings": self._jev_decision_policy.model_dump(mode="json", by_alias=True),
                "decisions": [item.model_dump(mode="json", by_alias=True) for item in decisions],
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
        return diagnostics_id, policy_id

    def propose_open_properties(
        self,
        context: RunContext,
        *,
        evidence_artifact_id: str,
        normalization_artifact_id: str,
        resolution_artifact_id: str | None,
        diagnostics_artifact_id: str | None,
    ) -> str | None:
        work = self._store(context)
        if not resolution_artifact_id or not diagnostics_artifact_id:
            work.event(
                "semantic.open_property_shadow_skipped",
                "ECLASS resolution/diagnostics are unavailable, so wildcard proposals were skipped.",
                metadata={"shadowMode": True},
            )
            return None

        package = work.load(evidence_artifact_id, ProductKnowledgePackage)
        normalization = work.load(normalization_artifact_id, NormalizationReport)
        resolution = work.load(resolution_artifact_id, EclassResolutionReport)
        diagnostics = work.load(diagnostics_artifact_id, EclassDiagnosticsReport)
        report: OpenPropertyProposalReport = build_open_property_proposals(
            package=package,
            normalization=normalization,
            eclass_resolution=resolution,
            eclass_diagnostics=diagnostics,
            policy_settings=self._jev_decision_policy,
            templates=self._templates,
        )
        artifact_id = work.put_model(
            "semantic/open-property-proposals-shadow.json",
            report,
            derived_from=(
                resolution_artifact_id,
                diagnostics_artifact_id,
                normalization_artifact_id,
            ),
        )
        disposition_counts: dict[str, int] = {}
        for proposal in report.proposals:
            disposition_counts[proposal.disposition.value] = (
                disposition_counts.get(proposal.disposition.value, 0) + 1
            )
        conflict_counts: dict[str, int] = {}
        for conflict in report.conflicts:
            conflict_counts[conflict.kind.value] = conflict_counts.get(conflict.kind.value, 0) + 1
        work.event(
            "semantic.open_property_shadow_completed",
            "Built shadow Technical Data ArbitraryProperty proposals and conflict diagnostics.",
            metadata={
                "artifactId": artifact_id,
                "dispositions": disposition_counts,
                "conflicts": conflict_counts,
                "shadowMode": True,
            },
        )
        return artifact_id

"""Diagnostics over multi-scope hierarchical Jev routing results."""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from pydantic import Field, model_validator

from mia_dpp.domain.base import WireModel
from mia_dpp.semantic.idta_routing import IdtaRoutingReport, IdtaRoutingTrace
from mia_dpp.semantic.models import ContextScope


class ChoiceDiagnostics(WireModel):
    """Derived uncertainty diagnostics for one non-deterministic Jev Choice."""

    question_id: str
    selected_choice: str
    selected_probability: float = Field(ge=0.0, le=1.0)
    top_probability: float = Field(ge=0.0, le=1.0)
    runner_up_probability: float = Field(ge=0.0, le=1.0)
    margin: float = Field(ge=0.0, le=1.0)
    runner_up_ratio: float = Field(ge=0.0)
    normalized_entropy: float = Field(ge=0.0, le=1.0)
    choice_is_argmax: bool


class RouteDiagnostics(WireModel):
    """Weakest-link diagnostics for one scope-specific hierarchical route."""

    context_view_id: str
    scope: ContextScope
    route_signature: str
    terminal_reason: str
    decisions: tuple[ChoiceDiagnostics, ...]
    minimum_selected_probability: float = Field(ge=0.0, le=1.0)
    minimum_margin: float = Field(ge=0.0, le=1.0)
    maximum_runner_up_ratio: float = Field(ge=0.0)
    maximum_normalized_entropy: float = Field(ge=0.0, le=1.0)
    all_choices_are_argmax: bool


class EvidenceRoutingDiagnostics(WireModel):
    """Cross-scope agreement and uncertainty for one source evidence item."""

    evidence_id: str
    routes: tuple[RouteDiagnostics, ...] = Field(min_length=1)
    consensus_signature: str
    consensus_count: int = Field(ge=1)
    scope_count: int = Field(ge=1)
    scope_agreement: float = Field(ge=0.0, le=1.0)
    distinct_route_count: int = Field(ge=1)
    minimum_selected_probability: float = Field(ge=0.0, le=1.0)
    minimum_margin: float = Field(ge=0.0, le=1.0)
    maximum_runner_up_ratio: float = Field(ge=0.0)
    maximum_normalized_entropy: float = Field(ge=0.0, le=1.0)
    all_choices_are_argmax: bool
    unresolved_scope_count: int = Field(ge=0)

    @model_validator(mode="after")
    def counts_are_consistent(self) -> EvidenceRoutingDiagnostics:
        if self.consensus_count > self.scope_count:
            raise ValueError("consensus_count cannot exceed scope_count")
        if self.unresolved_scope_count > self.scope_count:
            raise ValueError("unresolved_scope_count cannot exceed scope_count")
        return self


class RoutingDiagnosticsReport(WireModel):
    """Complete multi-scope diagnostic artifact."""

    evidence: tuple[EvidenceRoutingDiagnostics, ...]


def normalized_entropy(probabilities: dict[str, float]) -> float:
    """Return Shannon entropy normalized to [0, 1] for the option count."""

    if len(probabilities) <= 1:
        return 0.0
    entropy = -sum(
        probability * math.log(probability)
        for probability in probabilities.values()
        if probability > 0.0
    )
    return entropy / math.log(len(probabilities))


def choice_diagnostics(
    *,
    question_id: str,
    selected_choice: str,
    probabilities: dict[str, float],
) -> ChoiceDiagnostics:
    """Compute relative confidence features without treating them as calibrated correctness."""

    ranked = sorted(probabilities.items(), key=lambda item: item[1], reverse=True)
    top_choice, top_probability = ranked[0]
    runner_up_probability = ranked[1][1] if len(ranked) > 1 else 0.0
    selected_probability = probabilities[selected_choice]
    margin = max(0.0, top_probability - runner_up_probability)
    runner_up_ratio = (
        runner_up_probability / top_probability if top_probability > 0.0 else 1.0
    )
    return ChoiceDiagnostics(
        question_id=question_id,
        selected_choice=selected_choice,
        selected_probability=selected_probability,
        top_probability=top_probability,
        runner_up_probability=runner_up_probability,
        margin=margin,
        runner_up_ratio=runner_up_ratio,
        normalized_entropy=normalized_entropy(probabilities),
        choice_is_argmax=selected_choice == top_choice,
    )


def route_signature(trace: IdtaRoutingTrace) -> str:
    """Stable semantic destination used only for cross-scope comparison."""

    template = trace.selected_template_key or "-"
    path = "/".join(trace.selected_path) if trace.selected_path else "-"
    return f"{template}|{path}|{trace.terminal_reason}"


def diagnose_trace(trace: IdtaRoutingTrace) -> RouteDiagnostics:
    """Summarize only genuine Jev decisions; deterministic single-child steps add no uncertainty."""

    decisions = tuple(
        choice_diagnostics(
            question_id=step.decision.question_id,
            selected_choice=step.decision.choice,
            probabilities=step.decision.probabilities,
        )
        for step in trace.steps
        if not step.deterministic
    )
    if not decisions:
        minimum_selected_probability = 1.0
        minimum_margin = 1.0
        maximum_runner_up_ratio = 0.0
        maximum_normalized_entropy = 0.0
        all_choices_are_argmax = True
    else:
        minimum_selected_probability = min(
            item.selected_probability for item in decisions
        )
        minimum_margin = min(item.margin for item in decisions)
        maximum_runner_up_ratio = max(item.runner_up_ratio for item in decisions)
        maximum_normalized_entropy = max(
            item.normalized_entropy for item in decisions
        )
        all_choices_are_argmax = all(item.choice_is_argmax for item in decisions)
    return RouteDiagnostics(
        context_view_id=trace.context_view_id,
        scope=trace.scope,
        route_signature=route_signature(trace),
        terminal_reason=trace.terminal_reason,
        decisions=decisions,
        minimum_selected_probability=minimum_selected_probability,
        minimum_margin=minimum_margin,
        maximum_runner_up_ratio=maximum_runner_up_ratio,
        maximum_normalized_entropy=maximum_normalized_entropy,
        all_choices_are_argmax=all_choices_are_argmax,
    )


def build_routing_diagnostics(report: IdtaRoutingReport) -> RoutingDiagnosticsReport:
    """Compare all configured scope strategies for each evidence item."""

    grouped: dict[str, list[RouteDiagnostics]] = defaultdict(list)
    for trace in report.traces:
        grouped[trace.focus_evidence_id].append(diagnose_trace(trace))

    diagnostics: list[EvidenceRoutingDiagnostics] = []
    for evidence_id, routes_list in sorted(grouped.items()):
        routes = tuple(routes_list)
        counts = Counter(item.route_signature for item in routes)
        consensus_signature, consensus_count = counts.most_common(1)[0]
        scope_count = len(routes)
        diagnostics.append(
            EvidenceRoutingDiagnostics(
                evidence_id=evidence_id,
                routes=routes,
                consensus_signature=consensus_signature,
                consensus_count=consensus_count,
                scope_count=scope_count,
                scope_agreement=consensus_count / scope_count,
                distinct_route_count=len(counts),
                minimum_selected_probability=min(
                    item.minimum_selected_probability for item in routes
                ),
                minimum_margin=min(item.minimum_margin for item in routes),
                maximum_runner_up_ratio=max(
                    item.maximum_runner_up_ratio for item in routes
                ),
                maximum_normalized_entropy=max(
                    item.maximum_normalized_entropy for item in routes
                ),
                all_choices_are_argmax=all(
                    item.all_choices_are_argmax for item in routes
                ),
                unresolved_scope_count=sum(
                    item.terminal_reason == "__unresolved__" for item in routes
                ),
            )
        )
    return RoutingDiagnosticsReport(evidence=tuple(diagnostics))

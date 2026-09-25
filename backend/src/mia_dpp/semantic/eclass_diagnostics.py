"""Diagnostics and review priority for multi-scope ECLASS Jev decisions."""

from __future__ import annotations

from collections import Counter

from pydantic import Field, model_validator

from mia_dpp.domain.base import WireModel
from mia_dpp.semantic.decision_policy import (
    DecisionPolicySettings,
    DecisionPriority,
    EvidencePolicyDecision,
)
from mia_dpp.semantic.diagnostics import ChoiceDiagnostics, choice_diagnostics
from mia_dpp.semantic.eclass_resolution import (
    UNRESOLVED_ECLASS,
    EclassEvidenceResolution,
    EclassResolutionReport,
)


class EclassScopeDiagnostics(WireModel):
    """One context-specific ECLASS choice with distribution diagnostics."""

    scope: str
    context_view_id: str
    diagnostics: ChoiceDiagnostics


class EclassEvidenceDiagnostics(WireModel):
    """Cross-scope ECLASS agreement for one evidence item."""

    evidence_id: str
    retrieval_status: str
    scope_decisions: tuple[EclassScopeDiagnostics, ...] = ()
    consensus_choice: str | None = None
    consensus_count: int = Field(default=0, ge=0)
    scope_count: int = Field(default=0, ge=0)
    scope_agreement: float = Field(default=0.0, ge=0.0, le=1.0)
    distinct_choice_count: int = Field(default=0, ge=0)
    minimum_selected_probability: float = Field(default=0.0, ge=0.0, le=1.0)
    minimum_margin: float = Field(default=0.0, ge=0.0, le=1.0)
    maximum_runner_up_ratio: float = Field(default=0.0, ge=0.0)
    maximum_normalized_entropy: float = Field(default=0.0, ge=0.0, le=1.0)
    all_choices_are_argmax: bool = True
    unresolved_scope_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def counts_are_consistent(self) -> EclassEvidenceDiagnostics:
        if self.consensus_count > self.scope_count:
            raise ValueError("consensus_count cannot exceed scope_count")
        if self.unresolved_scope_count > self.scope_count:
            raise ValueError("unresolved_scope_count cannot exceed scope_count")
        return self


class EclassDiagnosticsReport(WireModel):
    """Diagnostics for every ECLASS resolution result."""

    evidence: tuple[EclassEvidenceDiagnostics, ...]


def _diagnose_result(result: EclassEvidenceResolution) -> EclassEvidenceDiagnostics:
    scoped = tuple(
        EclassScopeDiagnostics(
            scope=item.scope.value,
            context_view_id=item.context_view_id,
            diagnostics=choice_diagnostics(
                question_id=item.decision.question_id,
                selected_choice=item.decision.choice,
                probabilities=item.decision.probabilities,
            ),
        )
        for item in result.decisions
    )
    if not scoped:
        return EclassEvidenceDiagnostics(
            evidence_id=result.evidence_id,
            retrieval_status=result.retrieval_status,
        )

    choices = [item.diagnostics.selected_choice for item in scoped]
    consensus_choice, consensus_count = Counter(choices).most_common(1)[0]
    scope_count = len(scoped)
    return EclassEvidenceDiagnostics(
        evidence_id=result.evidence_id,
        retrieval_status=result.retrieval_status,
        scope_decisions=scoped,
        consensus_choice=consensus_choice,
        consensus_count=consensus_count,
        scope_count=scope_count,
        scope_agreement=consensus_count / scope_count,
        distinct_choice_count=len(set(choices)),
        minimum_selected_probability=min(item.diagnostics.selected_probability for item in scoped),
        minimum_margin=min(item.diagnostics.margin for item in scoped),
        maximum_runner_up_ratio=max(item.diagnostics.runner_up_ratio for item in scoped),
        maximum_normalized_entropy=max(item.diagnostics.normalized_entropy for item in scoped),
        all_choices_are_argmax=all(item.diagnostics.choice_is_argmax for item in scoped),
        unresolved_scope_count=sum(
            item.diagnostics.selected_choice == UNRESOLVED_ECLASS for item in scoped
        ),
    )


def build_eclass_diagnostics(
    report: EclassResolutionReport,
) -> EclassDiagnosticsReport:
    """Derive cross-scope diagnostics without any registry or model calls."""

    return EclassDiagnosticsReport(
        evidence=tuple(_diagnose_result(item) for item in report.results)
    )


def _strong_conflicting_choices(
    diagnostics: EclassEvidenceDiagnostics,
    settings: DecisionPolicySettings,
) -> bool:
    strong_choices = {
        item.diagnostics.selected_choice
        for item in diagnostics.scope_decisions
        if (
            item.diagnostics.selected_probability >= settings.alarm_strong_route_probability
            and item.diagnostics.choice_is_argmax
        )
    }
    return len(strong_choices) > 1


def classify_eclass_diagnostics(
    diagnostics: EclassEvidenceDiagnostics,
    settings: DecisionPolicySettings,
) -> EvidencePolicyDecision | None:
    """Reuse the routing policy shape for verified ECLASS classifications."""

    if diagnostics.scope_count == 0 or diagnostics.consensus_choice is None:
        return None

    if not diagnostics.all_choices_are_argmax:
        return EvidencePolicyDecision(
            evidence_id=diagnostics.evidence_id,
            priority=DecisionPriority.ALARM,
            consensus_signature=diagnostics.consensus_choice,
            reasons=("selected_choice_not_probability_argmax",),
        )

    if _strong_conflicting_choices(diagnostics, settings):
        return EvidencePolicyDecision(
            evidence_id=diagnostics.evidence_id,
            priority=DecisionPriority.ALARM,
            consensus_signature=diagnostics.consensus_choice,
            reasons=("strong_context_scopes_disagree",),
        )

    reasons: list[str] = []
    if diagnostics.unresolved_scope_count:
        reasons.append("one_or_more_scopes_unresolved")

    auto = (
        diagnostics.minimum_selected_probability >= settings.auto_min_selected_probability
        and diagnostics.minimum_margin >= settings.auto_min_margin
        and diagnostics.maximum_runner_up_ratio <= settings.auto_max_runner_up_ratio
        and diagnostics.maximum_normalized_entropy <= settings.auto_max_normalized_entropy
        and diagnostics.scope_agreement >= settings.auto_min_scope_agreement
        and diagnostics.unresolved_scope_count == 0
    )
    if auto:
        return EvidencePolicyDecision(
            evidence_id=diagnostics.evidence_id,
            priority=DecisionPriority.AUTO,
            consensus_signature=diagnostics.consensus_choice,
            reasons=("strong_and_stable_across_configured_scopes",),
        )

    optional = (
        diagnostics.minimum_selected_probability >= settings.optional_min_selected_probability
        and diagnostics.minimum_margin >= settings.optional_min_margin
        and diagnostics.maximum_runner_up_ratio <= settings.optional_max_runner_up_ratio
        and diagnostics.maximum_normalized_entropy <= settings.optional_max_normalized_entropy
        and diagnostics.scope_agreement >= settings.optional_min_scope_agreement
        and diagnostics.unresolved_scope_count == 0
    )
    if optional:
        return EvidencePolicyDecision(
            evidence_id=diagnostics.evidence_id,
            priority=DecisionPriority.OPTIONAL,
            consensus_signature=diagnostics.consensus_choice,
            reasons=("reasonable_but_below_auto_thresholds",),
        )

    if diagnostics.scope_agreement < 1.0:
        reasons.append("context_scopes_disagree")
    if diagnostics.minimum_margin < settings.optional_min_margin:
        reasons.append("runner_up_too_close")
    if diagnostics.maximum_runner_up_ratio > settings.optional_max_runner_up_ratio:
        reasons.append("runner_up_probability_material")
    if diagnostics.maximum_normalized_entropy > settings.optional_max_normalized_entropy:
        reasons.append("distribution_too_balanced")
    if diagnostics.minimum_selected_probability < settings.optional_min_selected_probability:
        reasons.append("selected_probability_low")
    if not reasons:
        reasons.append("requires_confirmation_under_current_policy")
    return EvidencePolicyDecision(
        evidence_id=diagnostics.evidence_id,
        priority=DecisionPriority.CONFIRM,
        consensus_signature=diagnostics.consensus_choice,
        reasons=tuple(dict.fromkeys(reasons)),
    )


def eclass_policy_decisions(
    report: EclassDiagnosticsReport,
    settings: DecisionPolicySettings,
) -> tuple[EvidencePolicyDecision, ...]:
    """Classify only evidence that reached bounded ECLASS Jev decisions."""

    decisions = (classify_eclass_diagnostics(item, settings) for item in report.evidence)
    return tuple(item for item in decisions if item is not None)

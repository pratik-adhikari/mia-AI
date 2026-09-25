"""Deterministic, tunable review policy over saved Jev routing diagnostics."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from mia_dpp.domain.base import WireModel
from mia_dpp.semantic.diagnostics import (
    EvidenceRoutingDiagnostics,
    RoutingDiagnosticsReport,
)


class DecisionPriority(StrEnum):
    AUTO = "auto"
    OPTIONAL = "optional"
    CONFIRM = "confirm"
    ALARM = "alarm"


class DecisionPolicySettings(WireModel):
    """Starting thresholds; tune from real reviewed data without rerunning Jev."""

    auto_min_selected_probability: float = Field(default=0.80, ge=0.0, le=1.0)
    auto_min_margin: float = Field(default=0.75, ge=0.0, le=1.0)
    auto_max_runner_up_ratio: float = Field(default=0.12, ge=0.0, le=1.0)
    auto_max_normalized_entropy: float = Field(default=0.45, ge=0.0, le=1.0)
    auto_min_scope_agreement: float = Field(default=1.0, ge=0.0, le=1.0)

    optional_min_selected_probability: float = Field(default=0.70, ge=0.0, le=1.0)
    optional_min_margin: float = Field(default=0.45, ge=0.0, le=1.0)
    optional_max_runner_up_ratio: float = Field(default=0.15, ge=0.0, le=1.0)
    optional_max_normalized_entropy: float = Field(default=0.65, ge=0.0, le=1.0)
    optional_min_scope_agreement: float = Field(default=2 / 3, ge=0.0, le=1.0)

    alarm_strong_route_probability: float = Field(default=0.80, ge=0.0, le=1.0)
    alarm_max_scope_agreement: float = Field(default=1 / 3, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def automatic_thresholds_are_stricter(self) -> DecisionPolicySettings:
        if self.auto_min_selected_probability < self.optional_min_selected_probability:
            raise ValueError("AUTO selected-probability threshold must be at least OPTIONAL")
        if self.auto_min_margin < self.optional_min_margin:
            raise ValueError("AUTO margin threshold must be at least OPTIONAL")
        if self.auto_max_runner_up_ratio > self.optional_max_runner_up_ratio:
            raise ValueError("AUTO runner-up ratio must be no greater than OPTIONAL")
        if self.auto_max_normalized_entropy > self.optional_max_normalized_entropy:
            raise ValueError("AUTO entropy threshold must be no greater than OPTIONAL")
        if self.auto_min_scope_agreement < self.optional_min_scope_agreement:
            raise ValueError("AUTO scope agreement must be at least OPTIONAL")
        return self


class EvidencePolicyDecision(WireModel):
    """Human-attention category derived entirely from stored diagnostics."""

    evidence_id: str
    priority: DecisionPriority
    consensus_signature: str
    reasons: tuple[str, ...] = Field(min_length=1)


class DecisionPolicyReport(WireModel):
    """Review-policy artifact that can be recomputed without model calls."""

    settings: DecisionPolicySettings
    decisions: tuple[EvidencePolicyDecision, ...]

    @model_validator(mode="after")
    def evidence_ids_are_unique(self) -> DecisionPolicyReport:
        identifiers = [item.evidence_id for item in self.decisions]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("decision-policy evidence IDs must be unique")
        return self


def _strong_conflicting_routes(
    diagnostics: EvidenceRoutingDiagnostics,
    settings: DecisionPolicySettings,
) -> bool:
    strong_signatures = {
        route.route_signature
        for route in diagnostics.routes
        if route.minimum_selected_probability >= settings.alarm_strong_route_probability
        and route.all_choices_are_argmax
    }
    return (
        len(strong_signatures) > 1
        and diagnostics.scope_agreement <= settings.alarm_max_scope_agreement
    )


def classify_evidence(
    diagnostics: EvidenceRoutingDiagnostics,
    settings: DecisionPolicySettings,
) -> EvidencePolicyDecision:
    """Classify attention priority; probabilities are relative evidence, not correctness claims."""

    reasons: list[str] = []

    if not diagnostics.all_choices_are_argmax:
        reasons.append("selected_choice_not_probability_argmax")
        return EvidencePolicyDecision(
            evidence_id=diagnostics.evidence_id,
            priority=DecisionPriority.ALARM,
            consensus_signature=diagnostics.consensus_signature,
            reasons=tuple(reasons),
        )

    if _strong_conflicting_routes(diagnostics, settings):
        reasons.append("strong_context_scopes_disagree")
        return EvidencePolicyDecision(
            evidence_id=diagnostics.evidence_id,
            priority=DecisionPriority.ALARM,
            consensus_signature=diagnostics.consensus_signature,
            reasons=tuple(reasons),
        )

    if diagnostics.unresolved_scope_count:
        reasons.append("one_or_more_scopes_unresolved")

    auto_checks = (
        diagnostics.minimum_selected_probability >= settings.auto_min_selected_probability,
        diagnostics.minimum_margin >= settings.auto_min_margin,
        diagnostics.maximum_runner_up_ratio <= settings.auto_max_runner_up_ratio,
        diagnostics.maximum_normalized_entropy <= settings.auto_max_normalized_entropy,
        diagnostics.scope_agreement >= settings.auto_min_scope_agreement,
        diagnostics.unresolved_scope_count == 0,
    )
    if all(auto_checks):
        reasons.append("strong_and_stable_across_configured_scopes")
        return EvidencePolicyDecision(
            evidence_id=diagnostics.evidence_id,
            priority=DecisionPriority.AUTO,
            consensus_signature=diagnostics.consensus_signature,
            reasons=tuple(reasons),
        )

    optional_checks = (
        diagnostics.minimum_selected_probability >= settings.optional_min_selected_probability,
        diagnostics.minimum_margin >= settings.optional_min_margin,
        diagnostics.maximum_runner_up_ratio <= settings.optional_max_runner_up_ratio,
        diagnostics.maximum_normalized_entropy <= settings.optional_max_normalized_entropy,
        diagnostics.scope_agreement >= settings.optional_min_scope_agreement,
        diagnostics.unresolved_scope_count == 0,
    )
    if all(optional_checks):
        reasons.append("reasonable_but_below_auto_thresholds")
        return EvidencePolicyDecision(
            evidence_id=diagnostics.evidence_id,
            priority=DecisionPriority.OPTIONAL,
            consensus_signature=diagnostics.consensus_signature,
            reasons=tuple(reasons),
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
        consensus_signature=diagnostics.consensus_signature,
        reasons=tuple(dict.fromkeys(reasons)),
    )


def apply_decision_policy(
    diagnostics: RoutingDiagnosticsReport,
    settings: DecisionPolicySettings,
) -> DecisionPolicyReport:
    """Apply only deterministic thresholds to already-saved routing diagnostics."""

    return DecisionPolicyReport(
        settings=settings,
        decisions=tuple(classify_evidence(item, settings) for item in diagnostics.evidence),
    )

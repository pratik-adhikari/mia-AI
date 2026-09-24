"""Multi-scope Jev diagnostics and review-policy tests."""

from __future__ import annotations

from mia_dpp.semantic.decision_policy import (
    DecisionPolicySettings,
    DecisionPriority,
    classify_evidence,
)
from mia_dpp.semantic.diagnostics import (
    EvidenceRoutingDiagnostics,
    RouteDiagnostics,
)
from mia_dpp.semantic.models import ContextScope


def _route(
    *,
    scope: ContextScope,
    signature: str = "technical_data|TechnicalData/TechnicalPropertyAreas|wildcard",
    selected_probability: float,
    margin: float,
    runner_up_ratio: float,
    entropy: float,
) -> RouteDiagnostics:
    return RouteDiagnostics(
        context_view_id=f"view-{scope.value}",
        scope=scope,
        route_signature=signature,
        terminal_reason="wildcard",
        decisions=(),
        minimum_selected_probability=selected_probability,
        minimum_margin=margin,
        maximum_runner_up_ratio=runner_up_ratio,
        maximum_normalized_entropy=entropy,
        all_choices_are_argmax=True,
    )


def _diagnostics(*routes: RouteDiagnostics) -> EvidenceRoutingDiagnostics:
    signatures = [route.route_signature for route in routes]
    consensus = max(set(signatures), key=signatures.count)
    consensus_count = signatures.count(consensus)
    return EvidenceRoutingDiagnostics(
        evidence_id="ev-1",
        routes=routes,
        consensus_signature=consensus,
        consensus_count=consensus_count,
        scope_count=len(routes),
        scope_agreement=consensus_count / len(routes),
        distinct_route_count=len(set(signatures)),
        minimum_selected_probability=min(
            route.minimum_selected_probability for route in routes
        ),
        minimum_margin=min(route.minimum_margin for route in routes),
        maximum_runner_up_ratio=max(
            route.maximum_runner_up_ratio for route in routes
        ),
        maximum_normalized_entropy=max(
            route.maximum_normalized_entropy for route in routes
        ),
        all_choices_are_argmax=True,
        unresolved_scope_count=0,
    )


def test_85_15_requires_confirmation_under_starting_policy() -> None:
    routes = tuple(
        _route(
            scope=scope,
            selected_probability=0.85,
            margin=0.70,
            runner_up_ratio=0.15 / 0.85,
            entropy=0.61,
        )
        for scope in (
            ContextScope.PROPERTY,
            ContextScope.SIBLINGS,
            ContextScope.FULL_PRODUCT,
        )
    )

    decision = classify_evidence(_diagnostics(*routes), DecisionPolicySettings())

    assert decision.priority is DecisionPriority.CONFIRM
    assert "runner_up_probability_material" in decision.reasons


def test_85_7_with_diffuse_tail_can_auto_resolve_when_scopes_agree() -> None:
    routes = tuple(
        _route(
            scope=scope,
            selected_probability=0.85,
            margin=0.78,
            runner_up_ratio=0.07 / 0.85,
            entropy=0.34,
        )
        for scope in (
            ContextScope.PROPERTY,
            ContextScope.SIBLINGS,
            ContextScope.FULL_PRODUCT,
        )
    )

    decision = classify_evidence(_diagnostics(*routes), DecisionPolicySettings())

    assert decision.priority is DecisionPriority.AUTO
    assert decision.reasons == ("strong_and_stable_across_configured_scopes",)


def test_strong_cross_scope_disagreement_is_alarm() -> None:
    routes = (
        _route(
            scope=ContextScope.PROPERTY,
            signature="technical_data|TechnicalData/TechnicalPropertyAreas|wildcard",
            selected_probability=0.91,
            margin=0.82,
            runner_up_ratio=0.06,
            entropy=0.20,
        ),
        _route(
            scope=ContextScope.SIBLINGS,
            signature="digital_nameplate|Nameplate/ManufacturerName|leaf",
            selected_probability=0.90,
            margin=0.80,
            runner_up_ratio=0.07,
            entropy=0.22,
        ),
        _route(
            scope=ContextScope.FULL_PRODUCT,
            signature="digital_nameplate|Nameplate/ProductDesignation|leaf",
            selected_probability=0.88,
            margin=0.77,
            runner_up_ratio=0.08,
            entropy=0.25,
        ),
    )

    decision = classify_evidence(_diagnostics(*routes), DecisionPolicySettings())

    assert decision.priority is DecisionPriority.ALARM
    assert decision.reasons == ("strong_context_scopes_disagree",)


def test_policy_thresholds_are_tunable_without_model_data_changes() -> None:
    routes = tuple(
        _route(
            scope=scope,
            selected_probability=0.82,
            margin=0.72,
            runner_up_ratio=0.10,
            entropy=0.40,
        )
        for scope in (
            ContextScope.PROPERTY,
            ContextScope.SIBLINGS,
            ContextScope.FULL_PRODUCT,
        )
    )
    diagnostics = _diagnostics(*routes)

    strict = classify_evidence(diagnostics, DecisionPolicySettings())
    relaxed = classify_evidence(
        diagnostics,
        DecisionPolicySettings(
            auto_min_margin=0.70,
            auto_max_runner_up_ratio=0.12,
            auto_max_normalized_entropy=0.45,
        ),
    )

    assert strict.priority is not DecisionPriority.AUTO
    assert relaxed.priority is DecisionPriority.AUTO

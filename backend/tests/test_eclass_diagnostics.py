"""ECLASS cross-scope diagnostics and policy tests."""

from __future__ import annotations

from mia_dpp.semantic.decision_policy import DecisionPolicySettings, DecisionPriority
from mia_dpp.semantic.eclass import EclassProperty
from mia_dpp.semantic.eclass_diagnostics import (
    build_eclass_diagnostics,
    classify_eclass_diagnostics,
)
from mia_dpp.semantic.eclass_resolution import (
    EclassEvidenceResolution,
    EclassResolutionReport,
    EclassRetrievalStatus,
    EclassScopeDecision,
    NO_ECLASS_MATCH,
    UNRESOLVED_ECLASS,
)
from mia_dpp.semantic.jev import ChoiceDecision
from mia_dpp.semantic.models import ContextScope


def _decision(
    *,
    scope: ContextScope,
    choice: str,
    probabilities: dict[str, float],
) -> EclassScopeDecision:
    return EclassScopeDecision(
        scope=scope,
        context_view_id=f"view-{scope.value}",
        decision=ChoiceDecision(
            question_id="eclass_property",
            choice=choice,
            probabilities=probabilities,
        ),
    )


def _result(*decisions: EclassScopeDecision) -> EclassEvidenceResolution:
    candidates = (
        EclassProperty(
            irdi="irdi-A",
            preferred_name="Concept A",
        ),
        EclassProperty(
            irdi="irdi-B",
            preferred_name="Concept B",
        ),
    )
    return EclassEvidenceResolution(
        evidence_id="ev-1",
        applicable=True,
        search_query="fixture",
        retrieval_status=EclassRetrievalStatus.CANDIDATES_VERIFIED,
        verified_candidates=candidates,
        decisions=decisions,
    )


def test_eclass_scopes_agree_and_can_auto_resolve_in_shadow_policy() -> None:
    probabilities = {
        "irdi-A": 0.85,
        "irdi-B": 0.07,
        NO_ECLASS_MATCH: 0.04,
        UNRESOLVED_ECLASS: 0.04,
    }
    report = EclassResolutionReport(
        provider_name="fixture",
        results=(
            _result(
                _decision(
                    scope=ContextScope.SIBLINGS,
                    choice="irdi-A",
                    probabilities=probabilities,
                ),
                _decision(
                    scope=ContextScope.FULL_PRODUCT,
                    choice="irdi-A",
                    probabilities=probabilities,
                ),
            ),
        ),
    )

    diagnostics = build_eclass_diagnostics(report).evidence[0]
    policy = classify_eclass_diagnostics(
        diagnostics,
        DecisionPolicySettings(),
    )

    assert diagnostics.consensus_choice == "irdi-A"
    assert diagnostics.scope_agreement == 1.0
    assert policy is not None
    assert policy.priority is DecisionPriority.AUTO


def test_two_strong_eclass_scopes_disagree_is_alarm() -> None:
    siblings = {
        "irdi-A": 0.91,
        "irdi-B": 0.04,
        NO_ECLASS_MATCH: 0.03,
        UNRESOLVED_ECLASS: 0.02,
    }
    whole_product = {
        "irdi-A": 0.03,
        "irdi-B": 0.90,
        NO_ECLASS_MATCH: 0.04,
        UNRESOLVED_ECLASS: 0.03,
    }
    report = EclassResolutionReport(
        provider_name="fixture",
        results=(
            _result(
                _decision(
                    scope=ContextScope.SIBLINGS,
                    choice="irdi-A",
                    probabilities=siblings,
                ),
                _decision(
                    scope=ContextScope.FULL_PRODUCT,
                    choice="irdi-B",
                    probabilities=whole_product,
                ),
            ),
        ),
    )

    diagnostics = build_eclass_diagnostics(report).evidence[0]
    policy = classify_eclass_diagnostics(
        diagnostics,
        DecisionPolicySettings(),
    )

    assert diagnostics.scope_agreement == 0.5
    assert diagnostics.distinct_choice_count == 2
    assert policy is not None
    assert policy.priority is DecisionPriority.ALARM
    assert policy.reasons == ("strong_context_scopes_disagree",)


def test_unclassified_retrieval_result_has_no_policy_decision() -> None:
    report = EclassResolutionReport(
        provider_name="fixture",
        results=(
            EclassEvidenceResolution(
                evidence_id="ev-1",
                applicable=True,
                search_query="unknown",
                retrieval_status=EclassRetrievalStatus.RETRIEVAL_EMPTY,
            ),
        ),
    )

    diagnostics = build_eclass_diagnostics(report).evidence[0]
    policy = classify_eclass_diagnostics(
        diagnostics,
        DecisionPolicySettings(),
    )

    assert diagnostics.scope_count == 0
    assert diagnostics.consensus_choice is None
    assert policy is None

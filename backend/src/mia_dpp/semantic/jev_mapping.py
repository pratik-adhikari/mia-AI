"""Turn bounded Jev IDTA routes into fixed-target proposals and retained non-matches."""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import date, datetime, time

from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.mappings import (
    EvidenceOutcome,
    EvidenceOutcomeStatus,
    FieldMapping,
    MappingAssessment,
    MappingBasis,
    MappingOrigin,
    MappingResult,
    MappingStatus,
    SemanticReviewItem,
)
from mia_dpp.domain.targets import RequirementKind, TemplateIndex
from mia_dpp.normalization import normalize_package
from mia_dpp.semantic import build_context_views
from mia_dpp.semantic.decision_policy import (
    DecisionPolicyReport,
    DecisionPolicySettings,
    DecisionPriority,
    apply_decision_policy,
)
from mia_dpp.semantic.diagnostics import build_routing_diagnostics
from mia_dpp.semantic.idta_routing import IdtaRoutingReport, route_views
from mia_dpp.semantic.jev import JevDecisionClient
from mia_dpp.semantic.models import ContextScope, ContextViewSet
from mia_dpp.tools.mapping.review import MappingReviewService
from mia_dpp.tools.mapping.targets import mapping_target


def _value_constraint(record_value: object, value_type: str | None) -> str | None:
    """Flag source values that the official scalar target cannot represent as supplied."""

    if not isinstance(record_value, (str, int, float, bool)):
        return "source value is not scalar"
    kind = (value_type or "xs:string").casefold()
    value = str(record_value)
    try:
        if "bool" in kind and value.casefold() not in {"true", "false", "0", "1"}:
            return "source value is not a target boolean"
        if any(token in kind for token in ("int", "integer", "long", "short", "byte")):
            int(value)
        elif any(token in kind for token in ("decimal", "double", "float")):
            float(value)
        elif "datetime" in kind:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        elif kind.endswith("date") or ":date" in kind:
            date.fromisoformat(value)
        elif kind.endswith("time") or ":time" in kind:
            time.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return f"source value is not valid for {value_type}"
    return None


def map_jev_routes(
    package: ProductKnowledgePackage,
    index: TemplateIndex,
    routing: IdtaRoutingReport,
    policy: DecisionPolicyReport,
    templates: OfficialTemplateRepository,
) -> tuple[MappingResult, tuple[SemanticReviewItem, ...]]:
    """Use only official fixed targets; wildcard routes need verified semantics later."""

    decisions = {item.evidence_id: item for item in policy.decisions}
    traces: dict[str, list] = {}
    for trace in routing.traces:
        traces.setdefault(trace.focus_evidence_id, []).append(trace)
    requirements = {
        (item.template_key, item.template_path): item
        for item in index.requirements
        if item.kind is RequirementKind.VALUE
        and not item.wildcard
        and "[]" not in item.template_path
        and item.semantic_id
    }
    selected: dict[str, tuple] = {}
    for record in package.evidence:
        decision = decisions.get(record.id)
        if decision is None:
            continue
        matching = [
            trace for trace in traces.get(record.id, ())
            if (
                f"{trace.selected_template_key or '-'}|"
                f"{'/'.join(trace.selected_path) if trace.selected_path else '-'}|"
                f"{trace.terminal_reason}"
            ) == decision.consensus_signature
        ]
        matching.sort(key=lambda item: item.scope is ContextScope.FULL_PRODUCT, reverse=True)
        if matching and matching[0].terminal_reason == "leaf":
            trace = matching[0]
            requirement = requirements.get((trace.selected_template_key, trace.selected_path))
            if requirement is not None:
                selected[record.id] = (requirement, decision)

    collisions = Counter(item[0].id for item in selected.values())
    mapped: list[FieldMapping] = []
    ambiguous: list[FieldMapping] = []
    unmatched: list[str] = []
    outcomes: list[EvidenceOutcome] = []

    for record in package.evidence:
        selected_item = selected.get(record.id)
        if selected_item is None:
            unmatched.append(record.id)
            decision = decisions.get(record.id)
            route = decision.consensus_signature if decision else "no Jev decision"
            reason = (
                f"Retained without an authoritative fixed target; Jev route: {route}. "
                "Open Technical Properties require a verified semantic identifier."
            )
            outcomes.append(
                EvidenceOutcome(
                    evidence_id=record.id,
                    status=EvidenceOutcomeStatus.UNMAPPED,
                    reason=reason[:600],
                    mapping_origin=MappingOrigin.SEMANTIC_ENGINE,
                )
            )
            continue

        requirement, decision = selected_item
        if not isinstance(record.value, (str, int, float, bool)):
            unmatched.append(record.id)
            outcomes.append(EvidenceOutcome(
                evidence_id=record.id,
                status=EvidenceOutcomeStatus.UNMAPPED,
                reason="Retained non-scalar source value; official AAS target requires a scalar.",
                mapping_origin=MappingOrigin.SEMANTIC_ENGINE,
            ))
            continue
        target = mapping_target(templates.load(requirement.template_key), requirement.template_path)
        priority = decision.priority
        constraint_reasons: list[str] = []
        if collisions[requirement.id] > 1:
            priority = DecisionPriority.ALARM
            constraint_reasons.append("multiple facts propose the same fixed target")
        if (
            record.unit
            and requirement.unit
            and record.unit.casefold() != requirement.unit.casefold()
        ):
            priority = DecisionPriority.ALARM
            constraint_reasons.append("source and official target units differ")
        if requirement.allowed_values and str(record.value) not in requirement.allowed_values:
            priority = DecisionPriority.ALARM
            constraint_reasons.append("source value is outside the official allowed values")
        value_constraint = _value_constraint(record.value, requirement.value_type)
        if value_constraint:
            priority = DecisionPriority.ALARM
            constraint_reasons.append(value_constraint)
        needs_review = priority in {DecisionPriority.CONFIRM, DecisionPriority.ALARM}
        reason = (
            "Jev selected this official IDTA target across source-context scopes; "
            f"review policy: {priority.value}; reasons: {', '.join(decision.reasons)}."
        )
        if constraint_reasons:
            reason += " Deterministic constraints: " + "; ".join(constraint_reasons) + "."
        mapping = FieldMapping(
            id="mapping-" + hashlib.sha256(
                f"{record.id}\0{requirement.id}".encode()
            ).hexdigest()[:24],
            evidence_id=record.id,
            source_field=record.source_label or record.predicate,
            source_value=f"{record.value} {record.unit}" if record.unit else str(record.value),
            target=target,
            assessment=MappingAssessment(
                basis=MappingBasis.SEMANTIC,
                review_required=needs_review,
                reason=reason,
                uncertainties=(*decision.reasons, *constraint_reasons) if needs_review else (),
            ),
            reasoning=reason,
            status=MappingStatus.REVIEW if needs_review else MappingStatus.AUTO,
            mapping_origin=MappingOrigin.SEMANTIC_ENGINE,
            review_priority=priority.value,
        )
        (ambiguous if needs_review else mapped).append(mapping)
        outcomes.append(
            EvidenceOutcome(
                evidence_id=record.id,
                status=(
                    EvidenceOutcomeStatus.UNCERTAIN
                    if needs_review else EvidenceOutcomeStatus.MAPPED
                ),
                requirement_id=requirement.id,
                reason=reason[:600],
                mapping_origin=MappingOrigin.SEMANTIC_ENGINE,
            )
        )

    result = MappingResult(
        mapped=tuple(mapped),
        ambiguous=tuple(ambiguous),
        unmatched_evidence_ids=tuple(unmatched),
        outcomes=tuple(outcomes),
    )
    return result, tuple(
        item
        for item in MappingReviewService(templates).complete_review(package, result, index)
        if item.mapping is not None and item.mapping.status is MappingStatus.REVIEW
    )


async def map_new_jev_evidence(
    *,
    package: ProductKnowledgePackage,
    evidence_ids: set[str],
    index: TemplateIndex,
    templates: OfficialTemplateRepository,
    decider: JevDecisionClient,
    scopes: tuple[ContextScope, ...],
    max_concurrency: int,
    settings: DecisionPolicySettings,
) -> tuple[MappingResult, tuple[SemanticReviewItem, ...], IdtaRoutingReport, DecisionPolicyReport]:
    """Route new facts with the full retained source context but publish only new outcomes."""

    normalization = normalize_package(package)
    views = build_context_views(package, normalization)
    focused = ContextViewSet(
        normalization_version=views.normalization_version,
        views=tuple(view for view in views.views if view.focus_evidence_id in evidence_ids),
    )
    routing = await route_views(
        decider=decider,
        repository=templates,
        selected_template_keys=tuple(item.key for item in index.selected_templates),
        package=package,
        normalization=normalization,
        context_views=focused,
        scopes=scopes,
        max_concurrency=max_concurrency,
    )
    policy = apply_decision_policy(build_routing_diagnostics(routing), settings)
    incremental = package.model_copy(
        update={"evidence": tuple(item for item in package.evidence if item.id in evidence_ids)}
    )
    result, reviews = map_jev_routes(incremental, index, routing, policy, templates)
    return result, reviews, routing, policy


def require_review_for_projection_collisions(mapping: MappingResult) -> MappingResult:
    """Keep later batches from automatically publishing duplicate fixed AAS slots."""

    counts = Counter(item.target.projection_identity for item in mapping.mapped)
    collided = {
        item.evidence_id
        for item in mapping.mapped
        if counts[item.target.projection_identity] > 1
    }
    if not collided:
        return mapping

    def demote(item: FieldMapping) -> FieldMapping:
        if item.evidence_id not in collided:
            return item
        reason = "Multiple source facts propose the same final AAS target."
        return item.model_copy(update={
            "status": MappingStatus.REVIEW,
            "review_priority": "alarm",
            "assessment": item.assessment.model_copy(update={
                "review_required": True,
                "reason": reason,
                "uncertainties": (*item.assessment.uncertainties, reason),
            }),
            "reasoning": reason,
        })

    return mapping.model_copy(update={
        "mapped": tuple(demote(item) for item in mapping.mapped),
        "outcomes": tuple(
            item.model_copy(update={
                "status": EvidenceOutcomeStatus.UNCERTAIN,
                "reason": "Multiple source facts propose the same final AAS target.",
            }) if item.evidence_id in collided else item
            for item in mapping.outcomes
        ),
    })

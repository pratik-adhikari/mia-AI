"""Hierarchical IDTA routing over authoritative template structure."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Mapping

from pydantic import Field, model_validator

from mia_dpp.aas.requirements import build_template_index
from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.base import WireModel
from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.targets import Requirement, RequirementKind, TemplateIndex
from mia_dpp.normalization.models import NormalizationReport, NormalizedEvidence
from mia_dpp.semantic.jev import ChoiceDecision, JevDecisionClient
from mia_dpp.semantic.models import ContextScope, ContextView, ContextViewSet

NO_IDTA_LOCATION = "__no_idta_location__"
UNRESOLVED = "__unresolved__"
_MAX_CONTEXT_RECORDS = 24
_MAX_CONTEXT_BYTES = 32_000
_MAX_ROUTING_REQUEST_BYTES = 36_000

_ROUTING_INSTRUCTIONS = """Choose the one official IDTA end target that best represents the
focus source fact, considering its value, unit, hierarchy, product, and neighbouring facts. Every
listed path comes from the selected official templates. Preserve the source value and meaning; do
not force a fact into a target merely because it is listed. Open/wildcard targets indicate only a
possible location, not a verified semantic property. Choose __no_idta_location__ when none fit,
or __unresolved__ when context is insufficient. Never invent a path or semantic identifier."""


class IdtaRoutingStep(WireModel):
    """One bounded decision while descending the template hierarchy."""

    depth: int = Field(ge=0)
    parent_path: tuple[str, ...] = ()
    options: tuple[str, ...] = Field(min_length=1)
    decision: ChoiceDecision
    deterministic: bool = False


class IdtaRoutingTrace(WireModel):
    """One complete top-down route for one evidence/context scope."""

    focus_evidence_id: str
    context_view_id: str
    scope: ContextScope
    selected_template_key: str | None = None
    selected_path: tuple[str, ...] = ()
    terminal_reason: str
    steps: tuple[IdtaRoutingStep, ...]
    request_state: dict[str, object] | None = None


class IdtaRoutingReport(WireModel):
    """Shadow results for all configured context scopes."""

    traces: tuple[IdtaRoutingTrace, ...]

    @model_validator(mode="after")
    def trace_pairs_are_unique(self) -> IdtaRoutingReport:
        pairs = [(item.focus_evidence_id, item.context_view_id) for item in self.traces]
        if len(pairs) != len(set(pairs)):
            raise ValueError("IDTA routing traces must be unique per evidence/context view")
        return self


def _requirement_identifier(requirement: Requirement) -> str:
    return f"{requirement.template_key}|{'/'.join(requirement.template_path)}"


def _requirement_criterion(requirement: Requirement) -> str:
    parts = [f"modelType={requirement.model_type}"]
    if requirement.description:
        parts.append(f"expected={' '.join(requirement.description.split())[:160]}")
    if requirement.value_type:
        parts.append(f"valueType={requirement.value_type}")
    if requirement.unit:
        parts.append(f"unit={requirement.unit}")
    if requirement.allowed_values:
        parts.append(f"allowedValues={','.join(requirement.allowed_values)[:120]}")
    if requirement.wildcard:
        parts.append("open/wildcard location; requires separate verified semantics")
    return "; ".join(parts)


def _evidence_payload(
    package: ProductKnowledgePackage,
    normalization: NormalizationReport,
    view: ContextView,
    *,
    max_bytes: int = _MAX_CONTEXT_BYTES,
) -> dict[str, object]:
    by_id = {item.id: item for item in package.evidence}
    normalized = {item.evidence_id: item for item in normalization.evidence}
    focus = by_id[view.focus_evidence_id]

    def one(identifier: str) -> dict[str, object]:
        record = by_id[identifier]
        derived: NormalizedEvidence = normalized[identifier]
        return {
            "id": record.id,
            "label": record.source_label or record.predicate,
            "rawValue": record.value,
            "rawUnit": record.unit,
            "contextPath": list(record.context_path),
            "normalized": derived.model_dump(mode="json", by_alias=True),
        }

    focus_section = focus.context_path[:1]
    candidates: list[str] = [focus.id]
    sections_seen = {focus_section}
    for identifier in view.evidence_ids:
        section = by_id[identifier].context_path[:1]
        if section not in sections_seen:
            candidates.append(identifier)
            sections_seen.add(section)
    candidates.extend(
        identifier for identifier in view.evidence_ids
        if by_id[identifier].context_path == focus.context_path
    )
    candidates.extend(
        identifier for identifier in view.evidence_ids
        if by_id[identifier].context_path[:1] == focus_section
    )
    candidates.extend(view.evidence_ids)

    section_counts: dict[str, int] = {}
    for identifier in view.evidence_ids:
        section = by_id[identifier].context_path[:1]
        name = section[0] if section else "(root)"
        section_counts[name] = section_counts.get(name, 0) + 1

    context_evidence: list[dict[str, object]] = []
    payload: dict[str, object] = {
        "focusEvidence": one(focus.id),
        "scope": view.scope.value,
        "scopePath": list(view.context_path),
        "contextEvidence": context_evidence,
        "contextEvidenceTotal": len(view.evidence_ids),
        "contextEvidenceOmitted": len(view.evidence_ids),
        "contextSections": section_counts,
        "productName": package.product_name,
    }
    selected: set[str] = set()
    current_bytes = len(json.dumps(payload, ensure_ascii=False).encode())
    for identifier in candidates:
        if identifier in selected:
            continue
        record = one(identifier)
        record_bytes = len(json.dumps(record, ensure_ascii=False).encode()) + 2
        if current_bytes + record_bytes > max_bytes:
            continue
        context_evidence.append(record)
        current_bytes += record_bytes
        selected.add(identifier)
        if len(selected) >= _MAX_CONTEXT_RECORDS:
            break
    payload["contextEvidenceOmitted"] = len(view.evidence_ids) - len(selected)
    return payload


async def _choose(
    decider: JevDecisionClient,
    *,
    question_id: str,
    state: Mapping[str, object],
    criteria: Mapping[str, str],
) -> ChoiceDecision:
    return await decider.choose(
        question_id=question_id,
        state=state,
        instructions=_ROUTING_INSTRUCTIONS,
        criteria=criteria,
    )


async def route_context_view(
    *,
    decider: JevDecisionClient,
    repository: OfficialTemplateRepository,
    selected_template_keys: tuple[str, ...],
    package: ProductKnowledgePackage,
    normalization: NormalizationReport,
    view: ContextView,
    index: TemplateIndex | None = None,
) -> IdtaRoutingTrace:
    """Rank every official end requirement in one bounded choice per context scope."""

    if not selected_template_keys:
        raise ValueError("IDTA routing requires at least one selected template")
    index = index or build_template_index(
        tuple(repository.load(key) for key in selected_template_keys)
    )

    # Structural placeholders cannot receive a source value. Keeping them in the
    # choice set dilutes Jev's probability across destinations we cannot map.
    by_choice = {
        _requirement_identifier(item): item
        for item in index.requirements
        if item.kind is RequirementKind.VALUE
    }
    criteria = {key: _requirement_criterion(item) for key, item in by_choice.items()}
    criteria[NO_IDTA_LOCATION] = "Valid product data, but none of the official end targets fit."
    criteria[UNRESOLVED] = "The available source context is insufficient to select an end target."
    criteria_bytes = len(json.dumps(criteria, ensure_ascii=False).encode())
    if criteria_bytes >= _MAX_ROUTING_REQUEST_BYTES:
        raise ValueError("selected official template choices exceed the Jev routing request budget")
    state = _evidence_payload(
        package,
        normalization,
        view,
        max_bytes=min(_MAX_CONTEXT_BYTES, _MAX_ROUTING_REQUEST_BYTES - criteria_bytes),
    )
    decision = await _choose(
        decider,
        question_id="template_target",
        state=state,
        criteria=criteria,
    )
    step = IdtaRoutingStep(
        depth=0,
        options=tuple(criteria),
        decision=decision,
    )
    if decision.choice in {NO_IDTA_LOCATION, UNRESOLVED}:
        return IdtaRoutingTrace(
            focus_evidence_id=view.focus_evidence_id,
            context_view_id=view.id,
            scope=view.scope,
            terminal_reason=decision.choice,
            steps=(step,),
            request_state=state,
        )

    selected = by_choice[decision.choice]
    return IdtaRoutingTrace(
        focus_evidence_id=view.focus_evidence_id,
        context_view_id=view.id,
        scope=view.scope,
        selected_template_key=selected.template_key,
        selected_path=selected.template_path,
        terminal_reason="wildcard" if selected.wildcard else "leaf",
        steps=(step,),
        request_state=state,
    )


async def route_views(
    *,
    decider: JevDecisionClient,
    repository: OfficialTemplateRepository,
    selected_template_keys: tuple[str, ...],
    package: ProductKnowledgePackage,
    normalization: NormalizationReport,
    context_views: ContextViewSet,
    scopes: Iterable[ContextScope],
    max_concurrency: int = 8,
) -> IdtaRoutingReport:
    """Run independently inspectable routing strategies for selected context scopes."""

    if max_concurrency < 1:
        raise ValueError("routing max_concurrency must be at least one")
    wanted = set(scopes)
    views = tuple(view for view in context_views.views if view.scope in wanted)
    semaphore = asyncio.Semaphore(max_concurrency)
    index = build_template_index(tuple(repository.load(key) for key in selected_template_keys))

    async def one(view: ContextView) -> IdtaRoutingTrace:
        async with semaphore:
            return await route_context_view(
                decider=decider,
                repository=repository,
                selected_template_keys=selected_template_keys,
                package=package,
                normalization=normalization,
                view=view,
                index=index,
            )

    return IdtaRoutingReport(traces=tuple(await asyncio.gather(*(one(view) for view in views))))

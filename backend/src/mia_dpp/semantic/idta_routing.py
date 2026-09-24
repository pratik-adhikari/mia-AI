"""Hierarchical IDTA routing over authoritative template structure."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping

from pydantic import Field, model_validator

from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.base import WireModel
from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.targets import SubmodelTemplate, TemplateElement
from mia_dpp.normalization.models import NormalizationReport, NormalizedEvidence
from mia_dpp.semantic.jev import ChoiceDecision, JevDecisionClient
from mia_dpp.semantic.models import ContextScope, ContextView, ContextViewSet

NO_IDTA_LOCATION = "__no_idta_location__"
UNRESOLVED = "__unresolved__"

_ROUTING_INSTRUCTIONS = """Choose only the IDTA structure that best represents the supplied
source fact in its hierarchy. Preserve the source meaning; do not force a fact into a target merely
because the target is available. Choose __no_idta_location__ when the fact is valid product data but
none of the listed structures should contain it. Choose __unresolved__ when the supplied context is
insufficient to decide. The application owns all template IDs and will validate the route."""


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


class IdtaRoutingReport(WireModel):
    """Shadow results for all configured context scopes."""

    traces: tuple[IdtaRoutingTrace, ...]

    @model_validator(mode="after")
    def trace_pairs_are_unique(self) -> IdtaRoutingReport:
        pairs = [(item.focus_evidence_id, item.context_view_id) for item in self.traces]
        if len(pairs) != len(set(pairs)):
            raise ValueError("IDTA routing traces must be unique per evidence/context view")
        return self


def _element_identifier(element: TemplateElement) -> str:
    return "/".join(element.path)


def _element_criterion(element: TemplateElement) -> str:
    parts = [
        f"path={'/'.join(element.path)}",
        f"modelType={element.model_type}",
    ]
    if element.id_short:
        parts.append(f"name={element.id_short}")
    if element.description:
        parts.append(f"description={' '.join(element.description.split())[:500]}")
    if element.wildcard:
        parts.append("open/wildcard element that accepts product-specific semantics")
    if element.cardinality is not None:
        parts.append(f"cardinality={element.cardinality.value}")
    return "; ".join(parts)


def _evidence_payload(
    package: ProductKnowledgePackage,
    normalization: NormalizationReport,
    view: ContextView,
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

    return {
        "focusEvidence": one(focus.id),
        "scope": view.scope.value,
        "scopePath": list(view.context_path),
        "contextEvidence": [one(identifier) for identifier in view.evidence_ids],
        "productName": package.product_name,
    }


def _template_criterion(template: SubmodelTemplate) -> str:
    return (
        f"template={template.release.family}; root={template.id_short}; "
        f"semanticId={template.semantic_id.primary_value}"
    )


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
) -> IdtaRoutingTrace:
    """Descend only through official children; single-child structure is deterministic."""

    templates = tuple(repository.load(key) for key in selected_template_keys)
    if not templates:
        raise ValueError("IDTA routing requires at least one selected template")

    state = _evidence_payload(package, normalization, view)
    steps: list[IdtaRoutingStep] = []
    root_criteria = {template.release.key: _template_criterion(template) for template in templates}
    root_criteria[NO_IDTA_LOCATION] = (
        "Valid product data, but no selected IDTA submodel should contain it."
    )
    root_criteria[UNRESOLVED] = "The available source context is insufficient to select a submodel."
    root_decision = await _choose(
        decider,
        question_id="submodel",
        state=state,
        criteria=root_criteria,
    )
    steps.append(
        IdtaRoutingStep(
            depth=0,
            options=tuple(root_criteria),
            decision=root_decision,
        )
    )
    if root_decision.choice in {NO_IDTA_LOCATION, UNRESOLVED}:
        return IdtaRoutingTrace(
            focus_evidence_id=view.focus_evidence_id,
            context_view_id=view.id,
            scope=view.scope,
            terminal_reason=root_decision.choice,
            steps=tuple(steps),
        )

    selected = next(
        template for template in templates if template.release.key == root_decision.choice
    )
    children = selected.elements
    selected_path: tuple[str, ...] = (selected.id_short,)
    depth = 1

    while children:
        if len(children) == 1:
            child = children[0]
            synthetic = ChoiceDecision(
                question_id=f"level_{depth}",
                choice=_element_identifier(child),
                probabilities={_element_identifier(child): 1.0},
            )
            steps.append(
                IdtaRoutingStep(
                    depth=depth,
                    parent_path=selected_path,
                    options=(_element_identifier(child),),
                    decision=synthetic,
                    deterministic=True,
                )
            )
        else:
            criteria = {_element_identifier(child): _element_criterion(child) for child in children}
            criteria[NO_IDTA_LOCATION] = (
                "The fact belongs to the selected submodel, but none of these child structures fit."
            )
            criteria[UNRESOLVED] = (
                "The source context is insufficient to choose among these children."
            )
            decision = await _choose(
                decider,
                question_id=f"level_{depth}",
                state={
                    **state,
                    "selectedTemplate": selected.release.key,
                    "currentPath": list(selected_path),
                },
                criteria=criteria,
            )
            steps.append(
                IdtaRoutingStep(
                    depth=depth,
                    parent_path=selected_path,
                    options=tuple(criteria),
                    decision=decision,
                )
            )
            if decision.choice in {NO_IDTA_LOCATION, UNRESOLVED}:
                return IdtaRoutingTrace(
                    focus_evidence_id=view.focus_evidence_id,
                    context_view_id=view.id,
                    scope=view.scope,
                    selected_template_key=selected.release.key,
                    selected_path=selected_path,
                    terminal_reason=decision.choice,
                    steps=tuple(steps),
                )
            child = next(
                element for element in children if _element_identifier(element) == decision.choice
            )

        selected_path = child.path
        if child.wildcard:
            return IdtaRoutingTrace(
                focus_evidence_id=view.focus_evidence_id,
                context_view_id=view.id,
                scope=view.scope,
                selected_template_key=selected.release.key,
                selected_path=selected_path,
                terminal_reason="wildcard",
                steps=tuple(steps),
            )
        if not child.children:
            return IdtaRoutingTrace(
                focus_evidence_id=view.focus_evidence_id,
                context_view_id=view.id,
                scope=view.scope,
                selected_template_key=selected.release.key,
                selected_path=selected_path,
                terminal_reason="leaf",
                steps=tuple(steps),
            )
        children = child.children
        depth += 1

    return IdtaRoutingTrace(
        focus_evidence_id=view.focus_evidence_id,
        context_view_id=view.id,
        scope=view.scope,
        selected_template_key=selected.release.key,
        selected_path=selected_path,
        terminal_reason="structural",
        steps=tuple(steps),
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

    async def one(view: ContextView) -> IdtaRoutingTrace:
        async with semaphore:
            return await route_context_view(
                decider=decider,
                repository=repository,
                selected_template_keys=selected_template_keys,
                package=package,
                normalization=normalization,
                view=view,
            )

    return IdtaRoutingReport(traces=tuple(await asyncio.gather(*(one(view) for view in views))))

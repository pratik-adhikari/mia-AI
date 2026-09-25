"""Hierarchical IDTA routing tests with a deterministic fake Jev decider."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime

from mia_dpp.aas.requirements import build_template_index
from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.evidence import (
    EvidenceRecord,
    EvidenceStatus,
    ProductKnowledgePackage,
    SourceLocation,
)
from mia_dpp.domain.targets import RequirementKind
from mia_dpp.normalization import normalize_package
from mia_dpp.semantic import ContextScope, build_context_views
from mia_dpp.semantic.idta_routing import NO_IDTA_LOCATION, UNRESOLVED, route_context_view
from mia_dpp.semantic.jev import ChoiceDecision


class _TechnicalPropertiesDecider:
    criteria: dict[str, str]

    async def choose(
        self,
        *,
        question_id: str,
        state: Mapping[str, object],
        instructions: str,
        criteria: Mapping[str, str],
    ) -> ChoiceDecision:
        del state, instructions
        self.criteria = dict(criteria)
        choice = next(
            identifier for identifier in criteria
            if identifier.endswith("/ArbitraryProperty")
            and identifier.startswith("technical_data|")
        )
        probabilities = dict.fromkeys(criteria, 0.0)
        probabilities[choice] = 1.0
        return ChoiceDecision(
            question_id=question_id,
            choice=choice,
            probabilities=probabilities,
        )


class _AbstainingDecider:
    async def choose(
        self,
        *,
        question_id: str,
        state: Mapping[str, object],
        instructions: str,
        criteria: Mapping[str, str],
    ) -> ChoiceDecision:
        del state, instructions
        probabilities = dict.fromkeys(criteria, 0.0)
        probabilities[NO_IDTA_LOCATION] = 1.0
        return ChoiceDecision(
            question_id=question_id,
            choice=NO_IDTA_LOCATION,
            probabilities=probabilities,
        )


def _package() -> ProductKnowledgePackage:
    evidence = EvidenceRecord(
        id="ev-1",
        predicate="source.repeat-accuracy",
        source_label="Repeat accuracy",
        value="+/-0.03 mm",
        context_path=("Technical Specifications", "Motion Performance"),
        source_uri="https://manufacturer.example/robot",
        source_content_sha256=hashlib.sha256(b"fixture").hexdigest(),
        source_location=SourceLocation(excerpt="Repeat accuracy +/-0.03 mm"),
        extraction_method="fixture",
        extractor_name="fixture",
        extractor_version="1",
        status=EvidenceStatus.OBSERVED,
        acquired_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return ProductKnowledgePackage(
        product_id="robot-1",
        product_name="Robot",
        evidence=(evidence,),
    )


def test_router_can_reach_open_technical_property_without_inventing_a_path() -> None:
    package = _package()
    normalization = normalize_package(package)
    contexts = build_context_views(package, normalization)
    view = next(item for item in contexts.views if item.scope is ContextScope.SIBLINGS)

    decider = _TechnicalPropertiesDecider()
    trace = asyncio.run(
        route_context_view(
            decider=decider,
            repository=OfficialTemplateRepository(),
            selected_template_keys=("digital_nameplate", "technical_data"),
            package=package,
            normalization=normalization,
            view=view,
        )
    )

    assert trace.selected_template_key == "technical_data"
    assert trace.terminal_reason == "wildcard"
    assert trace.selected_path == (
        "TechnicalData",
        "TechnicalPropertyAreas",
        "[]",
        "Section",
        "ArbitraryProperty",
    )
    assert trace.steps[0].decision.choice == (
        "technical_data|TechnicalData/TechnicalPropertyAreas/[]/Section/ArbitraryProperty"
    )
    assert all(step.decision.choice in step.options for step in trace.steps)
    index = build_template_index(tuple(
        OfficialTemplateRepository().load(key)
        for key in ("digital_nameplate", "technical_data")
    ))
    value_targets = {
        f"{item.template_key}|{'/'.join(item.template_path)}"
        for item in index.requirements
        if item.kind is RequirementKind.VALUE
    }
    assert set(decider.criteria) == value_targets | {NO_IDTA_LOCATION, UNRESOLVED}


def test_router_keeps_abstention_without_a_target() -> None:
    package = _package()
    normalization = normalize_package(package)
    contexts = build_context_views(package, normalization)
    view = next(item for item in contexts.views if item.scope is ContextScope.SIBLINGS)

    trace = asyncio.run(route_context_view(
        decider=_AbstainingDecider(),
        repository=OfficialTemplateRepository(),
        selected_template_keys=("digital_nameplate", "technical_data"),
        package=package,
        normalization=normalization,
        view=view,
    ))

    assert trace.terminal_reason == NO_IDTA_LOCATION
    assert trace.selected_template_key is None
    assert trace.selected_path == ()

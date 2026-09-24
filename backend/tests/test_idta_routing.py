"""Hierarchical IDTA routing tests with a deterministic fake Jev decider."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime

from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.evidence import (
    EvidenceRecord,
    EvidenceStatus,
    ProductKnowledgePackage,
    SourceLocation,
)
from mia_dpp.normalization import normalize_package
from mia_dpp.semantic import ContextScope, build_context_views
from mia_dpp.semantic.idta_routing import route_context_view
from mia_dpp.semantic.jev import ChoiceDecision


class _TechnicalPropertiesDecider:
    async def choose(
        self,
        *,
        question_id: str,
        state: Mapping[str, object],
        instructions: str,
        criteria: Mapping[str, str],
    ) -> ChoiceDecision:
        del state, instructions
        preferred = (
            "technical_data",
            "TechnicalPropertyAreas",
            "[]",
            "ArbitraryProperty",
        )
        choice = next(
            (
                identifier
                for suffix in preferred
                for identifier in criteria
                if identifier == suffix or identifier.endswith(f"/{suffix}")
            ),
            next(
                identifier
                for identifier in criteria
                if not identifier.startswith("__")
            ),
        )
        probabilities = {identifier: 0.0 for identifier in criteria}
        probabilities[choice] = 1.0
        return ChoiceDecision(
            question_id=question_id,
            choice=choice,
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

    trace = asyncio.run(
        route_context_view(
            decider=_TechnicalPropertiesDecider(),
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
        "ArbitraryProperty",
    )
    assert trace.steps[0].decision.choice == "technical_data"
    assert all(step.decision.choice in step.options for step in trace.steps)

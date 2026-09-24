"""Routing-aware ECLASS retrieval and bounded Jev concept tests."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime

from mia_dpp.domain.evidence import (
    EvidenceRecord,
    EvidenceStatus,
    ProductKnowledgePackage,
    SourceLocation,
)
from mia_dpp.normalization import normalize_package
from mia_dpp.semantic import ContextScope, build_context_views
from mia_dpp.semantic.eclass import (
    EclassProperty,
    EclassSearchCandidate,
)
from mia_dpp.semantic.eclass_resolution import (
    EclassRetrievalStatus,
    NO_ECLASS_MATCH,
    resolve_eclass_for_technical_properties,
)
from mia_dpp.semantic.idta_routing import IdtaRoutingReport, IdtaRoutingTrace
from mia_dpp.semantic.jev import ChoiceDecision


class _Provider:
    provider_name = "fixture-eclass"

    def __init__(self, *, empty: bool = False) -> None:
        self.empty = empty
        self.search_queries: list[str] = []
        self.lookups: list[str] = []

    async def search_properties(
        self,
        query: str,
        *,
        limit: int,
    ) -> tuple[EclassSearchCandidate, ...]:
        self.search_queries.append(query)
        if self.empty:
            return ()
        assert limit == 12
        return (
            EclassSearchCandidate(
                irdi="0173-1#02-AAO677#001",
                preferred_name="Position repeatability",
                definition="Repeatability of positioning.",
            ),
            EclassSearchCandidate(
                irdi="0173-1#02-OTHER#001",
                preferred_name="Positioning accuracy",
            ),
        )

    async def get_property(self, irdi: str) -> EclassProperty | None:
        self.lookups.append(irdi)
        if irdi.endswith("OTHER#001"):
            return None
        return EclassProperty(
            irdi=irdi,
            preferred_name="Position repeatability",
            definition="Repeatability of positioning.",
            data_type="REAL_MEASURE",
            unit_symbol="mm",
            release="15.0",
        )


class _Decider:
    def __init__(self, *, no_match: bool = False) -> None:
        self.no_match = no_match
        self.criteria_seen: list[tuple[str, ...]] = []

    async def choose(
        self,
        *,
        question_id: str,
        state: Mapping[str, object],
        instructions: str,
        criteria: Mapping[str, str],
    ) -> ChoiceDecision:
        del state, instructions
        self.criteria_seen.append(tuple(criteria))
        choice = (
            NO_ECLASS_MATCH
            if self.no_match
            else "0173-1#02-AAO677#001"
        )
        probabilities = {identifier: 0.0 for identifier in criteria}
        probabilities[choice] = 1.0
        return ChoiceDecision(
            question_id=question_id,
            choice=choice,
            probabilities=probabilities,
        )


def _record(
    identifier: str,
    label: str,
    value: str,
    context: tuple[str, ...],
) -> EvidenceRecord:
    return EvidenceRecord(
        id=identifier,
        predicate=f"source.{identifier}",
        source_label=label,
        value=value,
        context_path=context,
        source_uri="https://manufacturer.example/robot",
        source_content_sha256=hashlib.sha256(b"fixture").hexdigest(),
        source_location=SourceLocation(excerpt=f"{label}: {value}"),
        extraction_method="fixture",
        extractor_name="fixture",
        extractor_version="1",
        status=EvidenceStatus.OBSERVED,
        acquired_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _package() -> ProductKnowledgePackage:
    return ProductKnowledgePackage(
        product_id="robot-1",
        product_name="Robot",
        evidence=(
            _record(
                "ev-1",
                "Repeat accuracy",
                "+/-0.03 mm",
                ("Technical Specifications", "Motion Performance"),
            ),
            _record(
                "ev-2",
                "Manufacturer",
                "Example Robotics",
                ("Identification",),
            ),
        ),
    )


def _routing() -> IdtaRoutingReport:
    traces = []
    for scope in (
        ContextScope.PROPERTY,
        ContextScope.SIBLINGS,
        ContextScope.FULL_PRODUCT,
    ):
        traces.append(
            IdtaRoutingTrace(
                focus_evidence_id="ev-1",
                context_view_id=f"ctx-ev1-{scope.value}",
                scope=scope,
                selected_template_key="technical_data",
                selected_path=(
                    "TechnicalData",
                    "TechnicalPropertyAreas",
                    "[]",
                    "ArbitraryProperty",
                ),
                terminal_reason="wildcard",
                steps=(),
            )
        )
        traces.append(
            IdtaRoutingTrace(
                focus_evidence_id="ev-2",
                context_view_id=f"ctx-ev2-{scope.value}",
                scope=scope,
                selected_template_key="digital_nameplate",
                selected_path=("Nameplate", "ManufacturerName"),
                terminal_reason="leaf",
                steps=(),
            )
        )
    return IdtaRoutingReport(traces=tuple(traces))


def test_eclass_resolution_only_processes_open_technical_properties() -> None:
    package = _package()
    normalization = normalize_package(package)
    contexts = build_context_views(package, normalization)
    provider = _Provider()
    decider = _Decider()

    report = asyncio.run(
        resolve_eclass_for_technical_properties(
            provider=provider,
            decider=decider,
            package=package,
            normalization=normalization,
            context_views=contexts,
            routing=_routing(),
            scopes=(ContextScope.SIBLINGS, ContextScope.FULL_PRODUCT),
        )
    )

    by_id = {item.evidence_id: item for item in report.results}
    technical = by_id["ev-1"]
    manufacturer = by_id["ev-2"]

    assert report.provider_name == "fixture-eclass"
    assert technical.applicable is True
    assert technical.search_query == "Repeat accuracy"
    assert technical.retrieval_status == EclassRetrievalStatus.CANDIDATES_VERIFIED
    assert tuple(item.irdi for item in technical.search_hits) == (
        "0173-1#02-AAO677#001",
        "0173-1#02-OTHER#001",
    )
    assert technical.rejected_irdis == ("0173-1#02-OTHER#001",)
    assert tuple(item.irdi for item in technical.verified_candidates) == (
        "0173-1#02-AAO677#001",
    )
    assert len(technical.decisions) == 2
    assert all(
        scoped.decision.choice == "0173-1#02-AAO677#001"
        for scoped in technical.decisions
    )
    assert manufacturer.applicable is False
    assert manufacturer.retrieval_status == EclassRetrievalStatus.NOT_APPLICABLE
    assert provider.search_queries == ["Repeat accuracy"]


def test_empty_retrieval_is_not_misreported_as_jev_no_match() -> None:
    package = _package()
    normalization = normalize_package(package)
    contexts = build_context_views(package, normalization)

    report = asyncio.run(
        resolve_eclass_for_technical_properties(
            provider=_Provider(empty=True),
            decider=_Decider(no_match=True),
            package=package,
            normalization=normalization,
            context_views=contexts,
            routing=_routing(),
            scopes=(ContextScope.SIBLINGS,),
        )
    )

    result = next(item for item in report.results if item.evidence_id == "ev-1")
    assert result.retrieval_status == EclassRetrievalStatus.RETRIEVAL_EMPTY
    assert result.decisions == ()


def test_no_eclass_match_only_occurs_after_verified_candidates_exist() -> None:
    package = _package()
    normalization = normalize_package(package)
    contexts = build_context_views(package, normalization)

    report = asyncio.run(
        resolve_eclass_for_technical_properties(
            provider=_Provider(),
            decider=_Decider(no_match=True),
            package=package,
            normalization=normalization,
            context_views=contexts,
            routing=_routing(),
            scopes=(ContextScope.SIBLINGS,),
        )
    )

    result = next(item for item in report.results if item.evidence_id == "ev-1")
    assert result.verified_candidates
    assert len(result.decisions) == 1
    assert result.decisions[0].decision.choice == NO_ECLASS_MATCH

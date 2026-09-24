"""ECLASS retrieval, verification, and bounded Jev concept classification."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Iterable
from enum import StrEnum

from pydantic import Field, model_validator

from mia_dpp.domain.base import WireModel
from mia_dpp.domain.evidence import EvidenceRecord, ProductKnowledgePackage
from mia_dpp.normalization.models import NormalizationReport, NormalizedEvidence
from mia_dpp.semantic.eclass import (
    EclassProperty,
    EclassPropertyProvider,
    EclassSearchCandidate,
)
from mia_dpp.semantic.idta_routing import IdtaRoutingReport
from mia_dpp.semantic.jev import ChoiceDecision, JevDecisionClient
from mia_dpp.semantic.models import ContextScope, ContextView, ContextViewSet

NO_ECLASS_MATCH = "__no_eclass_match__"
UNRESOLVED_ECLASS = "__unresolved__"

_TECHNICAL_PROPERTY_PATH = (
    "TechnicalData",
    "TechnicalPropertyAreas",
    "[]",
    "ArbitraryProperty",
)

_ECLASS_INSTRUCTIONS = """Choose the ECLASS property whose authoritative definition best matches
the supplied source fact in context. Prefer semantic meaning over word similarity. Do not invent,
rewrite, or combine ECLASS concepts. Choose __no_eclass_match__ when none of the verified
candidates represent the source fact. Choose __unresolved__ when the candidates or source context
are insufficient to decide safely. Every listed IRDI was independently verified by the
application before this question was created."""


class EclassRetrievalStatus(StrEnum):
    """Registry retrieval state before bounded semantic classification."""

    NOT_APPLICABLE = "not_applicable"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    RETRIEVAL_EMPTY = "retrieval_empty"
    NO_VERIFIED_CANDIDATES = "no_verified_candidates"
    CANDIDATES_VERIFIED = "candidates_verified"


class EclassScopeDecision(WireModel):
    """One bounded concept decision using one contextual scope."""

    scope: ContextScope
    context_view_id: str
    decision: ChoiceDecision


class EclassEvidenceResolution(WireModel):
    """Complete retrieval and classification trace for one source fact."""

    evidence_id: str = Field(min_length=1)
    applicable: bool
    search_query: str | None = None
    retrieval_status: EclassRetrievalStatus
    search_hits: tuple[EclassSearchCandidate, ...] = ()
    rejected_irdis: tuple[str, ...] = ()
    verified_candidates: tuple[EclassProperty, ...] = ()
    decisions: tuple[EclassScopeDecision, ...] = ()

    @model_validator(mode="after")
    def classifications_use_verified_candidates(self) -> EclassEvidenceResolution:
        allowed = {item.irdi for item in self.verified_candidates}
        allowed.update({NO_ECLASS_MATCH, UNRESOLVED_ECLASS})
        for scoped in self.decisions:
            if scoped.decision.choice not in allowed:
                raise ValueError("ECLASS Jev choice must be a verified IRDI or explicit abstention")
            if set(scoped.decision.probabilities) != allowed:
                raise ValueError(
                    "ECLASS Jev probabilities must cover exactly the verified candidates "
                    "plus explicit abstentions"
                )
        return self


class EclassResolutionReport(WireModel):
    """Shadow ECLASS concept-resolution artifact."""

    provider_name: str | None = None
    results: tuple[EclassEvidenceResolution, ...]

    @model_validator(mode="after")
    def evidence_ids_are_unique(self) -> EclassResolutionReport:
        identifiers = [item.evidence_id for item in self.results]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("ECLASS resolution results must have unique evidence IDs")
        return self


def _route_key(trace: object) -> tuple[str | None, tuple[str, ...], str]:
    selected_template_key = getattr(trace, "selected_template_key")
    selected_path = getattr(trace, "selected_path")
    terminal_reason = getattr(trace, "terminal_reason")
    return selected_template_key, selected_path, terminal_reason


def technical_property_evidence_ids(
    routing: IdtaRoutingReport,
) -> frozenset[str]:
    """Select facts whose most common multi-scope route is the official open property slot."""

    grouped: dict[str, list[tuple[str | None, tuple[str, ...], str]]] = {}
    for trace in routing.traces:
        grouped.setdefault(trace.focus_evidence_id, []).append(_route_key(trace))

    eligible: set[str] = set()
    wanted = ("technical_data", _TECHNICAL_PROPERTY_PATH, "wildcard")
    for evidence_id, routes in grouped.items():
        counts = Counter(routes)
        wanted_count = counts[wanted]
        if wanted_count > len(routes) / 2:
            eligible.add(evidence_id)
    return frozenset(eligible)


def _context_view(
    context_views: ContextViewSet,
    *,
    evidence_id: str,
    scope: ContextScope,
) -> ContextView:
    matches = tuple(
        item
        for item in context_views.views
        if item.focus_evidence_id == evidence_id and item.scope is scope
    )
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one {scope.value} context view for evidence {evidence_id!r}"
        )
    return matches[0]


def _evidence_state(
    package: ProductKnowledgePackage,
    normalization: NormalizationReport,
    view: ContextView,
) -> dict[str, object]:
    records = {item.id: item for item in package.evidence}
    normalized = {item.evidence_id: item for item in normalization.evidence}

    def one(identifier: str) -> dict[str, object]:
        record: EvidenceRecord = records[identifier]
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
        "productName": package.product_name,
        "scope": view.scope.value,
        "focusEvidence": one(view.focus_evidence_id),
        "contextEvidence": [one(identifier) for identifier in view.evidence_ids],
    }


def _candidate_criterion(candidate: EclassProperty) -> str:
    parts = [
        f"IRDI={candidate.irdi}",
        f"preferredName={candidate.preferred_name}",
    ]
    if candidate.definition:
        parts.append(f"definition={' '.join(candidate.definition.split())[:600]}")
    if candidate.data_type:
        parts.append(f"dataType={candidate.data_type}")
    if candidate.unit_symbol:
        parts.append(f"unit={candidate.unit_symbol}")
    if candidate.unit_irdi:
        parts.append(f"unitIrdi={candidate.unit_irdi}")
    if candidate.release:
        parts.append(f"release={candidate.release}")
    return "; ".join(parts)


async def _verify_hits(
    provider: EclassPropertyProvider,
    hits: tuple[EclassSearchCandidate, ...],
    *,
    max_concurrency: int,
) -> tuple[tuple[EclassProperty, ...], tuple[str, ...]]:
    semaphore = asyncio.Semaphore(max_concurrency)

    async def one(hit: EclassSearchCandidate) -> tuple[str, EclassProperty | None]:
        async with semaphore:
            return hit.irdi, await provider.get_property(hit.irdi)

    verified: list[EclassProperty] = []
    rejected: list[str] = []
    for irdi, property_ in await asyncio.gather(*(one(hit) for hit in hits)):
        if property_ is None or property_.irdi != irdi:
            rejected.append(irdi)
            continue
        verified.append(property_)
    return tuple(verified), tuple(rejected)


async def _classify_candidates(
    *,
    decider: JevDecisionClient,
    package: ProductKnowledgePackage,
    normalization: NormalizationReport,
    context_views: ContextViewSet,
    evidence_id: str,
    candidates: tuple[EclassProperty, ...],
    scopes: Iterable[ContextScope],
) -> tuple[EclassScopeDecision, ...]:
    criteria = {item.irdi: _candidate_criterion(item) for item in candidates}
    criteria[NO_ECLASS_MATCH] = (
        "None of the verified ECLASS properties represents this source fact."
    )
    criteria[UNRESOLVED_ECLASS] = (
        "The available source context or candidate set is insufficient to decide safely."
    )

    decisions: list[EclassScopeDecision] = []
    for scope in scopes:
        view = _context_view(context_views, evidence_id=evidence_id, scope=scope)
        decision = await decider.choose(
            question_id="eclass_property",
            state=_evidence_state(package, normalization, view),
            instructions=_ECLASS_INSTRUCTIONS,
            criteria=criteria,
        )
        decisions.append(
            EclassScopeDecision(
                scope=scope,
                context_view_id=view.id,
                decision=decision,
            )
        )
    return tuple(decisions)


async def resolve_eclass_for_technical_properties(
    *,
    provider: EclassPropertyProvider | None,
    decider: JevDecisionClient | None,
    package: ProductKnowledgePackage,
    normalization: NormalizationReport,
    context_views: ContextViewSet,
    routing: IdtaRoutingReport,
    scopes: Iterable[ContextScope],
    search_limit: int = 12,
    verification_concurrency: int = 8,
) -> EclassResolutionReport:
    """Resolve only facts routed to the official open Technical Properties slot."""

    if search_limit < 1:
        raise ValueError("ECLASS search_limit must be at least one")
    if verification_concurrency < 1:
        raise ValueError("verification_concurrency must be at least one")

    eligible = technical_property_evidence_ids(routing)
    results: list[EclassEvidenceResolution] = []
    for record in package.evidence:
        if record.id not in eligible:
            results.append(
                EclassEvidenceResolution(
                    evidence_id=record.id,
                    applicable=False,
                    retrieval_status=EclassRetrievalStatus.NOT_APPLICABLE,
                )
            )
            continue

        query = (record.source_label or record.predicate).strip()
        if provider is None:
            results.append(
                EclassEvidenceResolution(
                    evidence_id=record.id,
                    applicable=True,
                    search_query=query,
                    retrieval_status=EclassRetrievalStatus.PROVIDER_UNAVAILABLE,
                )
            )
            continue

        hits = await provider.search_properties(query, limit=search_limit)
        if not hits:
            results.append(
                EclassEvidenceResolution(
                    evidence_id=record.id,
                    applicable=True,
                    search_query=query,
                    retrieval_status=EclassRetrievalStatus.RETRIEVAL_EMPTY,
                )
            )
            continue

        verified, rejected = await _verify_hits(
            provider,
            hits,
            max_concurrency=verification_concurrency,
        )
        if not verified:
            results.append(
                EclassEvidenceResolution(
                    evidence_id=record.id,
                    applicable=True,
                    search_query=query,
                    retrieval_status=EclassRetrievalStatus.NO_VERIFIED_CANDIDATES,
                    search_hits=hits,
                    rejected_irdis=rejected,
                )
            )
            continue

        decisions = (
            await _classify_candidates(
                decider=decider,
                package=package,
                normalization=normalization,
                context_views=context_views,
                evidence_id=record.id,
                candidates=verified,
                scopes=scopes,
            )
            if decider is not None
            else ()
        )
        results.append(
            EclassEvidenceResolution(
                evidence_id=record.id,
                applicable=True,
                search_query=query,
                retrieval_status=EclassRetrievalStatus.CANDIDATES_VERIFIED,
                search_hits=hits,
                rejected_irdis=rejected,
                verified_candidates=verified,
                decisions=decisions,
            )
        )

    return EclassResolutionReport(
        provider_name=provider.provider_name if provider is not None else None,
        results=tuple(results),
    )

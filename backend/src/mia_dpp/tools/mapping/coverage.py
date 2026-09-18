"""Conservative deterministic comparison of evidence with template requirements."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from mia_dpp.domain.evidence import EvidenceRecord, ProductKnowledgePackage
from mia_dpp.domain.mappings import (
    CoverageReport,
    CoverageStatus,
    MappingResult,
    MappingStatus,
    RequirementCoverage,
)
from mia_dpp.domain.targets import Requirement, RequirementKind, TemplateIndex

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "code",
        "data",
        "general",
        "information",
        "manufacturer",
        "name",
        "number",
        "of",
        "product",
        "the",
        "type",
    }
)
_STRONG_ALIASES: dict[str, frozenset[str]] = {
    "brand": frozenset({"manufacturer name"}),
    "manufacturer": frozenset({"manufacturer name"}),
    "manufacturer name": frozenset({"manufacturer name"}),
    "model": frozenset({"manufacturer product designation"}),
    "designation": frozenset({"manufacturer product designation"}),
    "product designation": frozenset({"manufacturer product designation"}),
    "product page url": frozenset({"uri of the product"}),
    "serial number": frozenset({"serial number"}),
    "year of construction": frozenset({"year of construction"}),
    "country of origin": frozenset({"country of origin"}),
}


@dataclass(frozen=True, slots=True)
class _Match:
    rank: int
    method: str
    explanation: str
    forced_ambiguity: bool = False


def coverage(
    package: ProductKnowledgePackage,
    inventory: TemplateIndex,
    *,
    mapping_result: MappingResult | None = None,
) -> CoverageReport:
    """Derive bidirectional target coverage without deleting source evidence."""

    evidence_by_id = {item.id: item for item in package.evidence}
    requirements_by_id = {item.id: item for item in inventory.requirements}
    matches: dict[tuple[str, str], _Match] = {}

    _add_mapping_matches(
        inventory,
        evidence_by_id,
        mapping_result,
        matches,
    )
    if mapping_result is None:
        _add_label_matches(package.evidence, inventory.requirements, matches)

    candidate_concepts: dict[str, set[str]] = defaultdict(set)
    for (requirement_id, evidence_id), match in matches.items():
        if match.rank == 1:
            candidate_concepts[evidence_id].add(_concept_key(requirements_by_id[requirement_id]))

    coverage = tuple(
        _coverage_for(
            requirement,
            package.evidence,
            matches,
            candidate_concepts,
        )
        for requirement in inventory.requirements
    )
    used = {
        evidence_id
        for item in coverage
        for evidence_id in (*item.supporting_evidence_ids, *item.candidate_evidence_ids)
    }
    analyzed_ids = tuple(evidence_by_id)
    unmatched = tuple(item for item in analyzed_ids if item not in used)
    return CoverageReport(
        inventory=inventory,
        coverage=coverage,
        analyzed_evidence_ids=analyzed_ids,
        unmatched_evidence_ids=unmatched,
    )


def _add_mapping_matches(
    inventory: TemplateIndex,
    evidence_by_id: dict[str, EvidenceRecord],
    mapping_result: MappingResult | None,
    matches: dict[tuple[str, str], _Match],
) -> None:
    if mapping_result is None:
        return
    by_target = {
        (item.template_key, item.template_release, item.template_path): item
        for item in inventory.requirements
    }
    for proposal in mapping_result.mapped:
        requirement = by_target.get(
            (
                proposal.target.template_key,
                proposal.target.template_release,
                proposal.target.template_path,
            )
        )
        if requirement is None or proposal.evidence_id not in evidence_by_id:
            continue
        if proposal.status in {MappingStatus.AUTO, MappingStatus.APPROVED}:
            match = _Match(
                rank=2,
                method="exact_mapping",
                explanation=(
                    "The existing deterministic mapper selected this exact template path."
                ),
            )
        elif proposal.status is MappingStatus.REVIEW:
            match = _Match(
                rank=1,
                method="mapping_review_candidate",
                explanation=(
                    "The existing deterministic mapper proposed this path, but its "
                    "review threshold was not met."
                ),
            )
        else:
            continue
        _record(matches, requirement.id, proposal.evidence_id, match)

    for proposal in mapping_result.ambiguous:
        requirement = by_target.get(
            (
                proposal.target.template_key,
                proposal.target.template_release,
                proposal.target.template_path,
            )
        )
        if requirement is None or proposal.evidence_id not in evidence_by_id:
            continue
        _record(
            matches,
            requirement.id,
            proposal.evidence_id,
            _Match(
                rank=1,
                method="mapping_destination_ambiguity",
                explanation="The deterministic mapper reported competing target meanings.",
                forced_ambiguity=True,
            ),
        )


def _add_label_matches(
    evidence: Sequence[EvidenceRecord],
    requirements: Sequence[Requirement],
    matches: dict[tuple[str, str], _Match],
) -> None:
    fixed_values = [
        requirement
        for requirement in requirements
        if requirement.kind is RequirementKind.VALUE and not requirement.wildcard
    ]
    for record in evidence:
        source_label = _normalize(record.source_label or record.predicate)
        alias_targets = _STRONG_ALIASES.get(source_label, frozenset())
        for requirement in fixed_values:
            if not _value_compatible(record, requirement):
                continue
            target_label = _normalize(requirement.id_short or "")
            if source_label == target_label:
                _record(
                    matches,
                    requirement.id,
                    record.id,
                    _Match(
                        rank=2,
                        method="exact_label",
                        explanation=(
                            "The normalized source label exactly matches the official idShort."
                        ),
                    ),
                )
            elif target_label in alias_targets:
                _record(
                    matches,
                    requirement.id,
                    record.id,
                    _Match(
                        rank=2,
                        method="known_alias",
                        explanation=(
                            f'Source label "{record.source_label or record.predicate}" is a '
                            f"reviewed alias for {requirement.id_short}."
                        ),
                    ),
                )
            elif _plausible_label(source_label, target_label):
                _record(
                    matches,
                    requirement.id,
                    record.id,
                    _Match(
                        rank=1,
                        method="label_candidate",
                        explanation=(
                            "Normalized source and target labels share specific terms, but "
                            "text similarity alone is not authoritative."
                        ),
                    ),
                )


def _coverage_for(
    requirement: Requirement,
    evidence: Sequence[EvidenceRecord],
    matches: dict[tuple[str, str], _Match],
    candidate_concepts: dict[str, set[str]],
) -> RequirementCoverage:
    by_id = {item.id: item for item in evidence}
    requirement_matches = [
        (evidence_id, match)
        for (requirement_id, evidence_id), match in matches.items()
        if requirement_id == requirement.id
    ]
    strong = [(evidence_id, match) for evidence_id, match in requirement_matches if match.rank == 2]
    if strong:
        strong_ids = tuple(evidence_id for evidence_id, _ in strong)
        distinct_values = {
            _normalized_value(by_id[evidence_id].value) for evidence_id in strong_ids
        }
        if len(distinct_values) > 1:
            return RequirementCoverage(
                requirement_id=requirement.id,
                status=CoverageStatus.AMBIGUOUS,
                candidate_evidence_ids=strong_ids,
                match_method="conflicting_deterministic_evidence",
                explanation="Multiple deterministic matches provide different source values.",
            )
        method = strong[0][1]
        return RequirementCoverage(
            requirement_id=requirement.id,
            status=CoverageStatus.SATISFIED,
            supporting_evidence_ids=strong_ids,
            match_method=method.method,
            explanation=method.explanation,
        )

    candidates = [(evidence_id, match) for evidence_id, match in requirement_matches]
    if not candidates:
        return RequirementCoverage(
            requirement_id=requirement.id,
            status=CoverageStatus.MISSING,
            match_method="none",
            explanation=(
                "No deterministic supporting evidence or conservative candidate was found."
            ),
        )
    candidate_ids = tuple(evidence_id for evidence_id, _ in candidates)
    values = {_normalized_value(by_id[evidence_id].value) for evidence_id in candidate_ids}
    ambiguous = (
        any(match.forced_ambiguity for _, match in candidates)
        or len(values) > 1
        or any(len(candidate_concepts[evidence_id]) > 1 for evidence_id in candidate_ids)
    )
    if ambiguous:
        return RequirementCoverage(
            requirement_id=requirement.id,
            status=CoverageStatus.AMBIGUOUS,
            candidate_evidence_ids=candidate_ids,
            match_method="competing_candidates",
            explanation=(
                "Multiple values or target meanings compete, so no deterministic choice is safe."
            ),
        )
    method = candidates[0][1]
    return RequirementCoverage(
        requirement_id=requirement.id,
        status=CoverageStatus.CANDIDATE,
        candidate_evidence_ids=candidate_ids,
        match_method=method.method,
        explanation=method.explanation,
    )


def _record(
    matches: dict[tuple[str, str], _Match],
    requirement_id: str,
    evidence_id: str,
    match: _Match,
) -> None:
    key = (requirement_id, evidence_id)
    existing = matches.get(key)
    if existing is None or match.rank > existing.rank:
        matches[key] = match


def _value_compatible(evidence: EvidenceRecord, requirement: Requirement) -> bool:
    if not isinstance(evidence.value, (str, int, float, bool)):
        return False
    if requirement.model_type == "File":
        value = str(evidence.value).casefold()
        if not (value.startswith(("http://", "https://", "file:")) or "." in value):
            return False
    if evidence.unit and requirement.unit:
        if _normalize(evidence.unit) != _normalize(requirement.unit):
            return False
    if requirement.allowed_values and str(evidence.value) not in requirement.allowed_values:
        return False
    return True


def _plausible_label(source: str, target: str) -> bool:
    source_tokens = set(_TOKEN.findall(source)) - _STOPWORDS
    target_tokens = set(_TOKEN.findall(target)) - _STOPWORDS
    if not source_tokens or not target_tokens:
        return False
    overlap = source_tokens & target_tokens
    if not overlap or max(len(token) for token in overlap) < 4:
        return False
    return source_tokens <= target_tokens or target_tokens <= source_tokens


def _concept_key(requirement: Requirement) -> str:
    label = requirement.id_short or "/".join(requirement.template_path)
    return _normalize(label)


def _normalized_value(value: object) -> str:
    return " ".join(str(value).casefold().split())


def _normalize(value: str) -> str:
    expanded = _CAMEL_BOUNDARY.sub(" ", value)
    return " ".join(_TOKEN.findall(expanded.casefold()))

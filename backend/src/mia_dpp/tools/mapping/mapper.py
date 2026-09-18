"""Cheap deterministic mapping over the dynamically selected target inventory."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.evidence import EvidenceRecord
from mia_dpp.domain.mappings import (
    FieldMapping,
    MappingAssessment,
    MappingBasis,
    MappingResult,
    MappingStatus,
)
from mia_dpp.domain.targets import Requirement, RequirementKind, TemplateIndex
from mia_dpp.tools.mapping.targets import mapping_target

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_ACRONYM_BOUNDARY = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_TOKEN = re.compile(r"[a-z0-9]+")
_ALIASES: dict[str, frozenset[str]] = {
    "brand": frozenset({"manufacturer name"}),
    "manufacturer": frozenset({"manufacturer name"}),
    "model": frozenset({"manufacturer product designation"}),
    "designation": frozenset({"manufacturer product designation"}),
    "product designation": frozenset({"manufacturer product designation"}),
    "product page url": frozenset({"uri of the product"}),
    "serial number": frozenset({"serial number"}),
    "sku": frozenset({"order code of manufacturer"}),
    "mpn": frozenset({"order code of manufacturer"}),
    "order code": frozenset({"order code of manufacturer"}),
    "year of construction": frozenset({"year of construction"}),
    "country of origin": frozenset({"country of origin"}),
}


class DeterministicWebsiteMapper:
    """Apply only high-confidence label/value matches to selected templates."""

    def __init__(
        self,
        repository: OfficialTemplateRepository,
        template_index: TemplateIndex,
    ) -> None:
        self._repository = repository
        self._index = template_index

    async def propose(self, evidence: Sequence[EvidenceRecord]) -> MappingResult:
        mapped: list[FieldMapping] = []
        ambiguous: list[FieldMapping] = []
        unmatched: list[str] = []
        claimed_requirements: set[str] = set()

        for record in evidence:
            candidates = [
                item
                for item in self._index.requirements
                if item.id not in claimed_requirements and self._matches(record, item)
            ]
            if not candidates:
                unmatched.append(record.id)
                continue
            mapping = self._mapping(record, candidates[0], ambiguous=len(candidates) > 1)
            if len(candidates) > 1:
                ambiguous.append(mapping)
            else:
                mapped.append(mapping)
                claimed_requirements.add(candidates[0].id)

        return MappingResult(
            mapped=tuple(mapped),
            ambiguous=tuple(ambiguous),
            unmatched_evidence_ids=tuple(unmatched),
        )

    def _mapping(
        self,
        evidence: EvidenceRecord,
        requirement: Requirement,
        *,
        ambiguous: bool,
    ) -> FieldMapping:
        target = mapping_target(
            self._repository.load(requirement.template_key),
            requirement.template_path,
        )
        identity = f"{evidence.id}\0{requirement.id}"
        assessment = MappingAssessment(
            basis=MappingBasis.EXACT,
            review_required=ambiguous,
            reason=(
                "The normalized source label uniquely matches the selected official target."
                if not ambiguous
                else "The source label matches more than one selected official target."
            ),
            uncertainties=("Multiple selected targets share this label.",) if ambiguous else (),
        )
        return FieldMapping(
            id="mapping-" + hashlib.sha256(identity.encode()).hexdigest()[:24],
            evidence_id=evidence.id,
            source_field=evidence.source_label or evidence.predicate,
            source_value=self._display_value(evidence),
            target=target,
            assessment=assessment,
            reasoning=assessment.reason,
            status=MappingStatus.REVIEW if ambiguous else MappingStatus.AUTO,
        )

    @staticmethod
    def _matches(evidence: EvidenceRecord, requirement: Requirement) -> bool:
        if (
            requirement.kind is not RequirementKind.VALUE
            or requirement.wildcard
            or requirement.semantic_id is None
        ):
            return False
        source = _normalize(evidence.source_label or evidence.predicate)
        target = _normalize(requirement.id_short or requirement.template_path[-1])
        accepted_targets = {source, *_ALIASES.get(source, frozenset())}
        if target not in accepted_targets:
            return False
        if (
            evidence.unit
            and requirement.unit
            and _normalize(evidence.unit) != _normalize(requirement.unit)
        ):
            return False
        return not requirement.allowed_values or str(evidence.value) in requirement.allowed_values

    @staticmethod
    def _display_value(evidence: EvidenceRecord) -> str:
        value = str(evidence.value)
        if evidence.unit and not value.endswith(evidence.unit):
            return f"{value} {evidence.unit}"
        return value


def _normalize(value: str) -> str:
    expanded = _ACRONYM_BOUNDARY.sub(" ", _CAMEL_BOUNDARY.sub(" ", value))
    return " ".join(_TOKEN.findall(expanded.casefold()))

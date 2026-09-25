"""Small deterministic syntax normalizer.

This module intentionally understands representation, not engineering semantics.
If syntax cannot be parsed safely, the raw value is retained and marked ambiguous
or unchanged for later Jev/contextual classification.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from mia_dpp.domain.evidence import EvidenceRecord, ProductKnowledgePackage
from mia_dpp.normalization.models import (
    NormalizationOrigin,
    NormalizationReport,
    NormalizationStatus,
    NormalizedEvidence,
    NormalizedValue,
    NormalizedValueKind,
)

NORMALIZER_VERSION = "syntax-v1"

_SPACE_RE = re.compile(r"\s+")
_TOLERANCE_RE = re.compile(
    r"^(?P<qualifier>±|\+/-)\s*(?P<number>[+-]?\d+(?:[.,]\d+)?)\s*(?P<unit>[^\d\s].*)?$"
)
_RANGE_RE = re.compile(
    r"^(?P<minimum>[+-]?\d+(?:[.,]\d+)?)\s*(?:-|\u2013|—|\.\.)\s*"
    r"(?P<maximum>[+-]?\d+(?:[.,]\d+)?)\s*(?P<unit>[^\d\s].*)?$"
)
_QUANTITY_RE = re.compile(
    r"^(?P<qualifier>~|≈|approx\.?|approximately)?\s*"
    r"(?P<number>[+-]?\d+(?:[.,]\d+)?)\s*(?P<unit>[^\d\s].*)?$",
    re.IGNORECASE,
)
_TRUE_VALUES = {"true", "yes"}
_FALSE_VALUES = {"false", "no"}


@dataclass(frozen=True, slots=True)
class _NumberParse:
    value: float | None
    ambiguity_reason: str | None = None


def _clean_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value)).strip()
    return _SPACE_RE.sub(" ", text)


def _clean_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    cleaned = _clean_text(unit)
    return cleaned or None


def _parse_number(token: str) -> _NumberParse:
    """Parse an unambiguous locale-light numeric token.

    A lone separator followed by exactly three digits (for example 1,200)
    is deliberately treated as ambiguous because it may be decimal or a
    thousands separator. Context/Jev may resolve it later.
    """

    if "," in token and "." in token:
        return _NumberParse(
            None,
            "number contains both comma and period separators; locale is ambiguous",
        )
    separator = "," if "," in token else "." if "." in token else None
    if separator is not None:
        integer, fraction = token.split(separator, maxsplit=1)
        if len(fraction) == 3 and integer.lstrip("+-") not in {"0", ""}:
            return _NumberParse(
                None,
                f"{token!r} may use {separator!r} as decimal or thousands separator",
            )
        token = token.replace(separator, ".")
    try:
        return _NumberParse(float(token))
    except ValueError:
        return _NumberParse(None, f"{token!r} is not a supported numeric representation")


def _resolved_unit(
    parsed_unit: str | None,
    explicit_unit: str | None,
) -> tuple[str | None, str | None]:
    embedded = _clean_unit(parsed_unit)
    explicit = _clean_unit(explicit_unit)
    if embedded and explicit and embedded.casefold() != explicit.casefold():
        return explicit, f"embedded unit {embedded!r} conflicts with explicit unit {explicit!r}"
    return explicit or embedded, None


def _ambiguous(
    record: EvidenceRecord,
    raw: str,
    reason: str,
) -> NormalizedEvidence:
    return NormalizedEvidence(
        evidence_id=record.id,
        label=record.source_label or record.predicate,
        context_path=record.context_path,
        value=NormalizedValue(
            raw=raw,
            kind=NormalizedValueKind.TEXT,
            unit=_clean_unit(record.unit),
        ),
        status=NormalizationStatus.AMBIGUOUS,
        origin=NormalizationOrigin.DETERMINISTIC,
        ambiguity_reason=reason,
    )


def normalize_evidence(record: EvidenceRecord) -> NormalizedEvidence:
    """Normalize syntax only; never infer an engineering concept."""

    raw = _clean_text(record.value)
    label = record.source_label or record.predicate
    lowered = raw.casefold()

    if lowered in _TRUE_VALUES | _FALSE_VALUES:
        return NormalizedEvidence(
            evidence_id=record.id,
            label=label,
            context_path=record.context_path,
            value=NormalizedValue(
                raw=raw,
                kind=NormalizedValueKind.BOOLEAN,
                boolean=lowered in _TRUE_VALUES,
            ),
            status=NormalizationStatus.NORMALIZED,
            origin=NormalizationOrigin.DETERMINISTIC,
        )

    tolerance = _TOLERANCE_RE.fullmatch(raw)
    if tolerance:
        number = _parse_number(tolerance.group("number"))
        if number.value is None:
            return _ambiguous(record, raw, number.ambiguity_reason or "ambiguous tolerance")
        unit, unit_error = _resolved_unit(tolerance.group("unit"), record.unit)
        if unit_error:
            return _ambiguous(record, raw, unit_error)
        return NormalizedEvidence(
            evidence_id=record.id,
            label=label,
            context_path=record.context_path,
            value=NormalizedValue(
                raw=raw,
                kind=NormalizedValueKind.TOLERANCE,
                number=abs(number.value),
                unit=unit,
                qualifier="plus_minus",
            ),
            status=NormalizationStatus.NORMALIZED,
            origin=NormalizationOrigin.DETERMINISTIC,
        )

    value_range = _RANGE_RE.fullmatch(raw)
    if value_range:
        minimum = _parse_number(value_range.group("minimum"))
        maximum = _parse_number(value_range.group("maximum"))
        if minimum.value is None or maximum.value is None:
            reason = minimum.ambiguity_reason or maximum.ambiguity_reason or "ambiguous range"
            return _ambiguous(record, raw, reason)
        if minimum.value > maximum.value:
            return _ambiguous(record, raw, "range minimum is greater than maximum")
        unit, unit_error = _resolved_unit(value_range.group("unit"), record.unit)
        if unit_error:
            return _ambiguous(record, raw, unit_error)
        return NormalizedEvidence(
            evidence_id=record.id,
            label=label,
            context_path=record.context_path,
            value=NormalizedValue(
                raw=raw,
                kind=NormalizedValueKind.RANGE,
                minimum=minimum.value,
                maximum=maximum.value,
                unit=unit,
            ),
            status=NormalizationStatus.NORMALIZED,
            origin=NormalizationOrigin.DETERMINISTIC,
        )

    quantity = _QUANTITY_RE.fullmatch(raw)
    if quantity:
        number = _parse_number(quantity.group("number"))
        if number.value is None:
            return _ambiguous(record, raw, number.ambiguity_reason or "ambiguous numeric value")
        unit, unit_error = _resolved_unit(quantity.group("unit"), record.unit)
        if unit_error:
            return _ambiguous(record, raw, unit_error)
        qualifier = quantity.group("qualifier")
        approximate_tokens = {"~", "≈", "approx.", "approx", "approximately"}
        normalized_qualifier = (
            "approximate" if qualifier and qualifier.casefold() in approximate_tokens else None
        )
        return NormalizedEvidence(
            evidence_id=record.id,
            label=label,
            context_path=record.context_path,
            value=NormalizedValue(
                raw=raw,
                kind=(
                    NormalizedValueKind.QUANTITY if unit is not None else NormalizedValueKind.NUMBER
                ),
                number=number.value,
                unit=unit,
                qualifier=normalized_qualifier,
            ),
            status=NormalizationStatus.NORMALIZED,
            origin=NormalizationOrigin.DETERMINISTIC,
        )

    return NormalizedEvidence(
        evidence_id=record.id,
        label=label,
        context_path=record.context_path,
        value=NormalizedValue(
            raw=raw,
            kind=NormalizedValueKind.TEXT,
            unit=_clean_unit(record.unit),
        ),
        status=NormalizationStatus.UNCHANGED,
        origin=NormalizationOrigin.UNCHANGED,
    )


def normalize_package(package: ProductKnowledgePackage) -> NormalizationReport:
    """Build a complete derived normalization report without mutating evidence."""

    return NormalizationReport(
        version=NORMALIZER_VERSION,
        evidence=tuple(normalize_evidence(record) for record in package.evidence),
    )

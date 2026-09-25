"""Build deterministic, multi-scope context views for Jev experiments."""

from __future__ import annotations

import hashlib
from collections import defaultdict

from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.normalization.models import NormalizationReport
from mia_dpp.semantic.models import ContextScope, ContextView, ContextViewSet


def _view_id(
    focus_evidence_id: str,
    scope: ContextScope,
    evidence_ids: tuple[str, ...],
    context_path: tuple[str, ...],
) -> str:
    payload = "\0".join(
        (
            focus_evidence_id,
            scope.value,
            "/".join(context_path),
            *evidence_ids,
        )
    )
    return "context-" + hashlib.sha256(payload.encode()).hexdigest()[:24]


def _common_section_path(path: tuple[str, ...]) -> tuple[str, ...]:
    """Treat the visible top-level source section as the broad section scope."""

    return path[:1] if path else ()


def build_context_views(
    package: ProductKnowledgePackage,
    normalization: NormalizationReport,
) -> ContextViewSet:
    """Create deterministic scopes without changing or merging source evidence."""

    evidence_ids = {item.id for item in package.evidence}
    normalized_ids = {item.evidence_id for item in normalization.evidence}
    if evidence_ids != normalized_ids:
        raise ValueError("normalization report must account for every evidence record exactly once")

    siblings: dict[tuple[str, ...], list[str]] = defaultdict(list)
    sections: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for record in package.evidence:
        siblings[record.context_path].append(record.id)
        sections[_common_section_path(record.context_path)].append(record.id)

    all_ids = tuple(item.id for item in package.evidence)
    views: list[ContextView] = []
    for record in package.evidence:
        parent_path = record.context_path[:-1] if record.context_path else ()
        scoped = (
            (ContextScope.PROPERTY, (record.id,), record.context_path),
            (ContextScope.PARENT, (record.id,), parent_path),
            (
                ContextScope.SIBLINGS,
                tuple(siblings[record.context_path]),
                record.context_path,
            ),
            (
                ContextScope.SECTION,
                tuple(sections[_common_section_path(record.context_path)]),
                _common_section_path(record.context_path),
            ),
            (ContextScope.FULL_PRODUCT, all_ids, ()),
        )
        seen: set[tuple[ContextScope, tuple[str, ...], tuple[str, ...]]] = set()
        for scope, scoped_ids, path in scoped:
            key = (scope, scoped_ids, path)
            if key in seen:
                continue
            seen.add(key)
            views.append(
                ContextView(
                    id=_view_id(record.id, scope, scoped_ids, path),
                    focus_evidence_id=record.id,
                    scope=scope,
                    evidence_ids=scoped_ids,
                    context_path=path,
                )
            )

    return ContextViewSet(
        normalization_version=normalization.version,
        views=tuple(views),
    )

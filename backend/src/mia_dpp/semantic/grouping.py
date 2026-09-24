"""Lossless semantic grouping experiments over immutable source evidence."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping
from enum import StrEnum

from pydantic import Field, model_validator

from mia_dpp.domain.base import WireModel
from mia_dpp.domain.evidence import EvidenceRecord, ProductKnowledgePackage
from mia_dpp.normalization.models import NormalizationReport, NormalizedEvidence
from mia_dpp.semantic.jev import (
    MAX_CHOICE_OPTIONS,
    ChoiceDecision,
    JevDecisionClient,
)
from mia_dpp.semantic.models import ContextScope, ContextView, ContextViewSet

NEW_GROUP = "__new_group__"
UNRESOLVED_GROUP = "__unresolved__"


class GroupingStrategy(StrEnum):
    HIERARCHY_BASELINE = "hierarchy_baseline"
    JEV_INCREMENTAL = "jev_incremental"


class GroupAssignmentStatus(StrEnum):
    EXISTING_GROUP = "existing_group"
    NEW_GROUP = "new_group"
    UNRESOLVED = "unresolved"
    BASELINE = "baseline"


class SemanticGroup(WireModel):
    """A non-destructive grouping view over source evidence IDs."""

    id: str = Field(pattern=r"^group-[0-9a-f]{24}$")
    strategy: GroupingStrategy
    scope: ContextScope | None = None
    anchor_evidence_id: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    source_paths: tuple[tuple[str, ...], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def anchor_is_a_member(self) -> SemanticGroup:
        if self.anchor_evidence_id not in self.evidence_ids:
            raise ValueError("group anchor must be one of the grouped evidence IDs")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("semantic group evidence IDs must be unique")
        return self


class GroupAssignment(WireModel):
    """One evidence-to-group decision; the EvidenceRecord itself is never changed."""

    evidence_id: str = Field(min_length=1)
    status: GroupAssignmentStatus
    group_id: str | None = Field(default=None, pattern=r"^group-[0-9a-f]{24}$")
    decision: ChoiceDecision | None = None

    @model_validator(mode="after")
    def status_matches_group(self) -> GroupAssignment:
        grouped = self.status is not GroupAssignmentStatus.UNRESOLVED
        if grouped and self.group_id is None:
            raise ValueError("grouped assignments require group_id")
        if not grouped and self.group_id is not None:
            raise ValueError("unresolved assignments must not have group_id")
        if self.status is GroupAssignmentStatus.BASELINE and self.decision is not None:
            raise ValueError("baseline assignments must not contain a Jev decision")
        return self


class SemanticGroupingRun(WireModel):
    """One independently inspectable grouping strategy result."""

    strategy: GroupingStrategy
    scope: ContextScope | None = None
    groups: tuple[SemanticGroup, ...]
    assignments: tuple[GroupAssignment, ...]

    @model_validator(mode="after")
    def run_accounts_for_each_evidence_once(self) -> SemanticGroupingRun:
        identifiers = [item.evidence_id for item in self.assignments]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("grouping assignments must have unique evidence IDs")
        group_ids = {item.id for item in self.groups}
        if any(
            item.group_id is not None and item.group_id not in group_ids
            for item in self.assignments
        ):
            raise ValueError("every assignment group_id must refer to a group in the run")
        return self


class PairwiseGroupingAgreement(WireModel):
    """Whether two facts co-cluster under each strategy, independent of group IDs."""

    left_evidence_id: str
    right_evidence_id: str
    grouped_together_by: tuple[str, ...]
    separated_by: tuple[str, ...]
    unresolved_by: tuple[str, ...]
    agreement: float = Field(ge=0.0, le=1.0)


class SemanticGroupingReport(WireModel):
    """All grouping strategies plus strategy-comparison diagnostics."""

    runs: tuple[SemanticGroupingRun, ...] = Field(min_length=1)
    pairwise_agreement: tuple[PairwiseGroupingAgreement, ...] = ()


def _group_id(
    *,
    strategy: GroupingStrategy,
    scope: ContextScope | None,
    anchor: str,
    discriminator: str,
) -> str:
    identity = "\0".join(
        (
            strategy.value,
            scope.value if scope is not None else "-",
            anchor,
            discriminator,
        )
    )
    return "group-" + hashlib.sha256(identity.encode()).hexdigest()[:24]


def _record_payload(
    record: EvidenceRecord,
    normalized: NormalizedEvidence,
) -> dict[str, object]:
    return {
        "id": record.id,
        "label": record.source_label or record.predicate,
        "rawValue": record.value,
        "rawUnit": record.unit,
        "contextPath": list(record.context_path),
        "normalized": normalized.model_dump(mode="json", by_alias=True),
    }


def hierarchy_baseline(package: ProductKnowledgePackage) -> SemanticGroupingRun:
    """Group only exact source hierarchy siblings; this is a non-semantic baseline."""

    buckets: dict[tuple[str, ...], list[EvidenceRecord]] = defaultdict(list)
    for record in package.evidence:
        buckets[record.context_path].append(record)

    groups: list[SemanticGroup] = []
    assignments: list[GroupAssignment] = []
    for path, records in buckets.items():
        anchor = records[0].id
        group_id = _group_id(
            strategy=GroupingStrategy.HIERARCHY_BASELINE,
            scope=None,
            anchor=anchor,
            discriminator="/".join(path),
        )
        groups.append(
            SemanticGroup(
                id=group_id,
                strategy=GroupingStrategy.HIERARCHY_BASELINE,
                anchor_evidence_id=anchor,
                evidence_ids=tuple(item.id for item in records),
                source_paths=tuple(dict.fromkeys(item.context_path for item in records)),
            )
        )
        assignments.extend(
            GroupAssignment(
                evidence_id=record.id,
                status=GroupAssignmentStatus.BASELINE,
                group_id=group_id,
            )
            for record in records
        )
    return SemanticGroupingRun(
        strategy=GroupingStrategy.HIERARCHY_BASELINE,
        groups=tuple(groups),
        assignments=tuple(assignments),
    )


def _group_criterion(
    group: SemanticGroup,
    records: Mapping[str, EvidenceRecord],
    *,
    max_members: int = 8,
) -> str:
    members = []
    for identifier in group.evidence_ids[:max_members]:
        record = records[identifier]
        label = record.source_label or record.predicate
        members.append(
            f"{label}={record.value!s} @ {' / '.join(record.context_path) or '<root>'}"
        )
    suffix = (
        f"; plus {len(group.evidence_ids) - max_members} more members"
        if len(group.evidence_ids) > max_members
        else ""
    )
    return (
        f"Existing semantic group anchored by evidence {group.anchor_evidence_id}; "
        f"members: {'; '.join(members)}{suffix}"
    )


def _view_for(
    context_views: ContextViewSet,
    evidence_id: str,
    scope: ContextScope,
) -> ContextView:
    matches = [
        item
        for item in context_views.views
        if item.focus_evidence_id == evidence_id and item.scope is scope
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected one {scope.value} context view for evidence {evidence_id!r}"
        )
    return matches[0]


_GROUPING_INSTRUCTIONS = """Assign the focus fact to an existing semantic group only when it
describes the same engineering/property concept or a strongly coherent concept family as that
group's current members. Hierarchy and sibling context matter. Do not merge facts merely because
they occur near each other. Choose __new_group__ when the fact is meaningful but does not belong to
any current group. Choose __unresolved__ when the available context is insufficient to decide.
This operation only assigns group metadata; it must not invent, rewrite, or discard product data."""


async def jev_incremental_grouping(
    *,
    decider: JevDecisionClient,
    package: ProductKnowledgePackage,
    normalization: NormalizationReport,
    context_views: ContextViewSet,
    scope: ContextScope,
    max_groups: int = 200,
) -> SemanticGroupingRun:
    """Incrementally build bounded groups with explicit NEW_GROUP and UNRESOLVED options."""

    if max_groups < 1:
        raise ValueError("max_groups must be at least one")
    maximum_supported_groups = MAX_CHOICE_OPTIONS - 2
    if max_groups > maximum_supported_groups:
        raise ValueError(
            f"max_groups cannot exceed {maximum_supported_groups}; "
            "NEW_GROUP and UNRESOLVED consume two Jev options"
        )
    records = {item.id: item for item in package.evidence}
    normalized = {item.evidence_id: item for item in normalization.evidence}
    if set(records) != set(normalized):
        raise ValueError("normalization must account for all evidence before grouping")

    groups: list[SemanticGroup] = []
    assignments: list[GroupAssignment] = []
    for record in package.evidence:
        if not groups:
            group_id = _group_id(
                strategy=GroupingStrategy.JEV_INCREMENTAL,
                scope=scope,
                anchor=record.id,
                discriminator="first",
            )
            groups.append(
                SemanticGroup(
                    id=group_id,
                    strategy=GroupingStrategy.JEV_INCREMENTAL,
                    scope=scope,
                    anchor_evidence_id=record.id,
                    evidence_ids=(record.id,),
                    source_paths=(record.context_path,),
                )
            )
            assignments.append(
                GroupAssignment(
                    evidence_id=record.id,
                    status=GroupAssignmentStatus.NEW_GROUP,
                    group_id=group_id,
                )
            )
            continue

        if len(groups) > max_groups:
            raise ValueError(
                f"grouping created {len(groups)} groups, above configured maximum {max_groups}"
            )

        view = _view_for(context_views, record.id, scope)
        criteria = {
            group.id: _group_criterion(group, records)
            for group in groups
        }
        criteria[NEW_GROUP] = (
            "The focus fact is meaningful, but none of the existing semantic groups fit it."
        )
        criteria[UNRESOLVED_GROUP] = (
            "The available source hierarchy/context is insufficient to decide grouping safely."
        )
        decision = await decider.choose(
            question_id="semantic_group",
            state={
                "productName": package.product_name,
                "scope": scope.value,
                "focusEvidence": _record_payload(record, normalized[record.id]),
                "contextEvidence": [
                    _record_payload(records[identifier], normalized[identifier])
                    for identifier in view.evidence_ids
                ],
            },
            instructions=_GROUPING_INSTRUCTIONS,
            criteria=criteria,
        )

        if decision.choice == UNRESOLVED_GROUP:
            assignments.append(
                GroupAssignment(
                    evidence_id=record.id,
                    status=GroupAssignmentStatus.UNRESOLVED,
                    decision=decision,
                )
            )
            continue

        if decision.choice == NEW_GROUP:
            if len(groups) >= max_groups:
                assignments.append(
                    GroupAssignment(
                        evidence_id=record.id,
                        status=GroupAssignmentStatus.UNRESOLVED,
                        decision=decision,
                    )
                )
                continue
            group_id = _group_id(
                strategy=GroupingStrategy.JEV_INCREMENTAL,
                scope=scope,
                anchor=record.id,
                discriminator=str(len(groups)),
            )
            groups.append(
                SemanticGroup(
                    id=group_id,
                    strategy=GroupingStrategy.JEV_INCREMENTAL,
                    scope=scope,
                    anchor_evidence_id=record.id,
                    evidence_ids=(record.id,),
                    source_paths=(record.context_path,),
                )
            )
            assignments.append(
                GroupAssignment(
                    evidence_id=record.id,
                    status=GroupAssignmentStatus.NEW_GROUP,
                    group_id=group_id,
                    decision=decision,
                )
            )
            continue

        group_index = next(
            index for index, group in enumerate(groups) if group.id == decision.choice
        )
        group = groups[group_index]
        groups[group_index] = group.model_copy(
            update={
                "evidence_ids": (*group.evidence_ids, record.id),
                "source_paths": tuple(
                    dict.fromkeys((*group.source_paths, record.context_path))
                ),
            }
        )
        assignments.append(
            GroupAssignment(
                evidence_id=record.id,
                status=GroupAssignmentStatus.EXISTING_GROUP,
                group_id=group.id,
                decision=decision,
            )
        )

    return SemanticGroupingRun(
        strategy=GroupingStrategy.JEV_INCREMENTAL,
        scope=scope,
        groups=tuple(groups),
        assignments=tuple(assignments),
    )


def _assignment_map(
    run: SemanticGroupingRun,
) -> dict[str, GroupAssignment]:
    return {item.evidence_id: item for item in run.assignments}


def compare_grouping_runs(
    runs: Iterable[SemanticGroupingRun],
) -> tuple[PairwiseGroupingAgreement, ...]:
    """Compare partitions by same-group relation instead of incomparable group IDs."""

    runs = tuple(runs)
    if not runs:
        return ()
    evidence_ids = tuple(item.evidence_id for item in runs[0].assignments)
    expected = set(evidence_ids)
    if any({item.evidence_id for item in run.assignments} != expected for run in runs):
        raise ValueError("all grouping runs must cover the same evidence IDs")

    maps = [(run, _assignment_map(run)) for run in runs]
    comparisons: list[PairwiseGroupingAgreement] = []
    for left_index, left in enumerate(evidence_ids):
        for right in evidence_ids[left_index + 1 :]:
            together: list[str] = []
            separated: list[str] = []
            unresolved: list[str] = []
            votes: list[bool] = []
            for run, assignments in maps:
                strategy_name = (
                    f"{run.strategy.value}:{run.scope.value}"
                    if run.scope is not None
                    else run.strategy.value
                )
                left_assignment = assignments[left]
                right_assignment = assignments[right]
                if (
                    left_assignment.group_id is None
                    or right_assignment.group_id is None
                ):
                    unresolved.append(strategy_name)
                    continue
                same = left_assignment.group_id == right_assignment.group_id
                votes.append(same)
                (together if same else separated).append(strategy_name)
            if votes:
                majority = max(sum(votes), len(votes) - sum(votes))
                agreement = majority / len(votes)
            else:
                agreement = 0.0
            comparisons.append(
                PairwiseGroupingAgreement(
                    left_evidence_id=left,
                    right_evidence_id=right,
                    grouped_together_by=tuple(together),
                    separated_by=tuple(separated),
                    unresolved_by=tuple(unresolved),
                    agreement=agreement,
                )
            )
    return tuple(comparisons)


async def build_grouping_report(
    *,
    decider: JevDecisionClient | None,
    package: ProductKnowledgePackage,
    normalization: NormalizationReport,
    context_views: ContextViewSet,
    scopes: Iterable[ContextScope],
    max_groups: int = 200,
) -> SemanticGroupingReport:
    """Run hierarchy baseline plus each configured Jev grouping scope."""

    runs: list[SemanticGroupingRun] = [hierarchy_baseline(package)]
    if decider is not None:
        for scope in scopes:
            runs.append(
                await jev_incremental_grouping(
                    decider=decider,
                    package=package,
                    normalization=normalization,
                    context_views=context_views,
                    scope=scope,
                    max_groups=max_groups,
                )
            )
    return SemanticGroupingReport(
        runs=tuple(runs),
        pairwise_agreement=compare_grouping_runs(runs),
    )

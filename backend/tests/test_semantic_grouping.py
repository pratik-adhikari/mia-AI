"""Lossless semantic grouping tests."""

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
from mia_dpp.semantic.grouping import (
    NEW_GROUP,
    UNRESOLVED_GROUP,
    GroupAssignmentStatus,
    GroupingStrategy,
    build_grouping_report,
    hierarchy_baseline,
    jev_incremental_grouping,
)
from mia_dpp.semantic.jev import ChoiceDecision


class _GroupingDecider:
    async def choose(
        self,
        *,
        question_id: str,
        state: Mapping[str, object],
        instructions: str,
        criteria: Mapping[str, str],
    ) -> ChoiceDecision:
        del instructions
        focus = state["focusEvidence"]
        assert isinstance(focus, dict)
        label = str(focus["label"])

        if label == "Max velocity":
            choice = next(identifier for identifier in criteria if identifier.startswith("group-"))
        elif label == "Voltage":
            choice = NEW_GROUP
        else:
            choice = UNRESOLVED_GROUP

        probabilities = dict.fromkeys(criteria, 0.0)
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
                "Max velocity",
                "2 m/s",
                ("Technical Specifications", "Motion Performance"),
            ),
            _record(
                "ev-3",
                "Voltage",
                "48 V",
                ("Technical Specifications", "Electrical"),
            ),
            _record(
                "ev-4",
                "Adaptive feature",
                "manufacturer-specific mode",
                ("Capabilities", "Advanced"),
            ),
        ),
    )


def test_hierarchy_baseline_is_lossless_metadata_only() -> None:
    package = _package()
    before = package.model_dump_json(by_alias=True)

    run = hierarchy_baseline(package)

    assert package.model_dump_json(by_alias=True) == before
    assert run.strategy is GroupingStrategy.HIERARCHY_BASELINE
    assert len(run.assignments) == len(package.evidence)
    assert all(item.status is GroupAssignmentStatus.BASELINE for item in run.assignments)
    motion = next(group for group in run.groups if group.evidence_ids == ("ev-1", "ev-2"))
    assert motion.source_paths == (("Technical Specifications", "Motion Performance"),)


def test_jev_grouping_keeps_new_and_unresolved_outcomes_explicit() -> None:
    package = _package()
    normalization = normalize_package(package)
    contexts = build_context_views(package, normalization)
    before = package.model_dump_json(by_alias=True)

    run = asyncio.run(
        jev_incremental_grouping(
            decider=_GroupingDecider(),
            package=package,
            normalization=normalization,
            context_views=contexts,
            scope=ContextScope.FULL_PRODUCT,
        )
    )

    by_evidence = {item.evidence_id: item for item in run.assignments}
    assert package.model_dump_json(by_alias=True) == before
    assert by_evidence["ev-1"].status is GroupAssignmentStatus.NEW_GROUP
    assert by_evidence["ev-2"].status is GroupAssignmentStatus.EXISTING_GROUP
    assert by_evidence["ev-1"].group_id == by_evidence["ev-2"].group_id
    assert by_evidence["ev-3"].status is GroupAssignmentStatus.NEW_GROUP
    assert by_evidence["ev-3"].group_id != by_evidence["ev-1"].group_id
    assert by_evidence["ev-4"].status is GroupAssignmentStatus.UNRESOLVED
    assert by_evidence["ev-4"].group_id is None
    assert by_evidence["ev-4"].decision is not None


def test_group_limit_turns_additional_new_group_into_unresolved() -> None:
    package = _package()
    normalization = normalize_package(package)
    contexts = build_context_views(package, normalization)

    run = asyncio.run(
        jev_incremental_grouping(
            decider=_GroupingDecider(),
            package=package,
            normalization=normalization,
            context_views=contexts,
            scope=ContextScope.FULL_PRODUCT,
            max_groups=1,
        )
    )

    by_evidence = {item.evidence_id: item for item in run.assignments}
    assert len(run.groups) == 1
    assert by_evidence["ev-3"].status is GroupAssignmentStatus.UNRESOLVED
    assert by_evidence["ev-3"].group_id is None


def test_grouping_report_compares_partitions_not_group_identifiers() -> None:
    package = _package()
    normalization = normalize_package(package)
    contexts = build_context_views(package, normalization)

    report = asyncio.run(
        build_grouping_report(
            decider=_GroupingDecider(),
            package=package,
            normalization=normalization,
            context_views=contexts,
            scopes=(ContextScope.FULL_PRODUCT,),
        )
    )

    assert len(report.runs) == 2
    assert report.runs[0].strategy is GroupingStrategy.HIERARCHY_BASELINE
    pair = next(
        item
        for item in report.pairwise_agreement
        if {item.left_evidence_id, item.right_evidence_id} == {"ev-1", "ev-2"}
    )
    assert pair.agreement == 1.0
    assert "hierarchy_baseline" in pair.grouped_together_by
    assert "jev_incremental:full_product" in pair.grouped_together_by

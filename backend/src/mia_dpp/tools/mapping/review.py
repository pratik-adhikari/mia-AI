"""Trusted construction and application of complete mapping reviews."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal

from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.evidence import (
    EvidenceRecord,
    EvidenceStatus,
    ProductKnowledgePackage,
    SourceLocation,
    SourceType,
)
from mia_dpp.domain.mappings import (
    EvidenceOutcome,
    EvidenceOutcomeStatus,
    FieldMapping,
    LlmReview,
    MappingAssessment,
    MappingBasis,
    MappingOrigin,
    MappingResult,
    MappingStatus,
    SemanticReviewItem,
)
from mia_dpp.domain.targets import Requirement, RequirementKind, TemplateIndex
from mia_dpp.tools.mapping.coverage import coverage
from mia_dpp.tools.mapping.models import SemanticMappingRun
from mia_dpp.tools.mapping.targets import mapping_target

ReviewDecision = Literal[
    "keep",
    "change_target",
    "unmapped",
    "irrelevant",
    "reject",
    "approve",
    "correct",
]


class MappingReviewService:
    """Keep model proposals constrained while humans remain authoritative."""

    def __init__(self, repository: OfficialTemplateRepository) -> None:
        self._repository = repository

    def apply_semantic_run(
        self,
        package: ProductKnowledgePackage,
        deterministic: MappingResult,
        template_index: TemplateIndex,
        run: SemanticMappingRun,
    ) -> MappingResult:
        """Create complete trusted mapping state after the validated batch response."""

        evidence_by_id = {item.id: item for item in package.evidence}
        requirements = {item.id: item for item in template_index.requirements}
        deterministic_by_id = {item.evidence_id: item for item in deterministic.mapped}
        mapped = list(deterministic.mapped)
        ambiguous: list[FieldMapping] = []
        unmatched: list[str] = []
        irrelevant: list[str] = []
        outcomes: list[EvidenceOutcome] = []

        for decision in run.result.decisions:
            authoritative = deterministic_by_id.get(decision.evidence_id)
            if authoritative is not None:
                requirement_id = self._requirement_id(authoritative, template_index)
                outcomes.append(
                    EvidenceOutcome(
                        evidence_id=decision.evidence_id,
                        status=EvidenceOutcomeStatus.MAPPED,
                        requirement_id=requirement_id,
                        reason="Preserved authoritative deterministic mapping.",
                        mapping_origin=MappingOrigin.DETERMINISTIC,
                    )
                )
                continue

            if decision.status in {"mapped", "uncertain"}:
                selected_id = decision.requirement_id or next(
                    iter(decision.alternative_requirement_ids), None
                )
                if selected_id is not None:
                    requirement = requirements[selected_id]
                    proposal = self._semantic_mapping(
                        evidence_by_id[decision.evidence_id],
                        requirement,
                        decision.reason,
                        alternatives=decision.alternative_requirement_ids,
                    )
                    if decision.status == "mapped":
                        mapped.append(proposal)
                    else:
                        ambiguous.append(proposal)
                else:
                    unmatched.append(decision.evidence_id)
                outcomes.append(
                    EvidenceOutcome(
                        evidence_id=decision.evidence_id,
                        status=(
                            EvidenceOutcomeStatus.MAPPED
                            if decision.status == "mapped"
                            else EvidenceOutcomeStatus.UNCERTAIN
                        ),
                        requirement_id=decision.requirement_id,
                        alternative_requirement_ids=decision.alternative_requirement_ids,
                        reason=decision.reason,
                        mapping_origin=MappingOrigin.SEMANTIC_AGENT,
                    )
                )
            elif decision.status == "irrelevant":
                irrelevant.append(decision.evidence_id)
                outcomes.append(
                    EvidenceOutcome(
                        evidence_id=decision.evidence_id,
                        status=EvidenceOutcomeStatus.IRRELEVANT,
                        reason=decision.reason,
                        mapping_origin=MappingOrigin.SEMANTIC_AGENT,
                    )
                )
            else:
                unmatched.append(decision.evidence_id)
                outcomes.append(
                    EvidenceOutcome(
                        evidence_id=decision.evidence_id,
                        status=EvidenceOutcomeStatus.UNMAPPED,
                        reason=decision.reason,
                        mapping_origin=MappingOrigin.SEMANTIC_AGENT,
                    )
                )

        result = MappingResult(
            mapped=tuple(mapped),
            ambiguous=tuple(ambiguous),
            unmatched_evidence_ids=tuple(unmatched),
            irrelevant_evidence_ids=tuple(irrelevant),
            outcomes=tuple(outcomes),
        )
        self.validate_complete_accounting(package, result)
        return result

    @staticmethod
    def validate_complete_accounting(
        package: ProductKnowledgePackage,
        mapping_result: MappingResult,
    ) -> None:
        expected = {item.id for item in package.evidence}
        actual = {item.evidence_id for item in mapping_result.outcomes}
        if actual != expected:
            raise ValueError("mapping result must account for every evidence record exactly once")

    @staticmethod
    def cycle_id(
        package: ProductKnowledgePackage,
        template_index: TemplateIndex,
        mapping_result: MappingResult,
    ) -> str:
        payload = {
            "evidence": [
                {
                    "id": item.id,
                    "label": item.source_label,
                    "value": item.value,
                    "unit": item.unit,
                    "context": item.context_path,
                }
                for item in package.evidence
            ],
            "templates": [
                item.model_dump(mode="json") for item in template_index.selected_templates
            ],
            "outcomes": [item.model_dump(mode="json") for item in mapping_result.outcomes],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return "cycle-" + hashlib.sha256(encoded.encode()).hexdigest()[:24]

    def complete_review(
        self,
        package: ProductKnowledgePackage,
        mapping_result: MappingResult,
        template_index: TemplateIndex,
    ) -> tuple[SemanticReviewItem, ...]:
        """Build one review row for every evidence record, including non-matches."""

        self.validate_complete_accounting(package, mapping_result)
        cycle_id = self.cycle_id(package, template_index, mapping_result)
        mappings = {
            item.evidence_id: item
            for item in (
                *mapping_result.mapped,
                *mapping_result.ambiguous,
                *mapping_result.rejected,
            )
        }
        outcomes = {item.evidence_id: item for item in mapping_result.outcomes}
        return tuple(
            SemanticReviewItem(
                id="review-" + hashlib.sha256(f"{cycle_id}\0{record.id}".encode()).hexdigest()[:24],
                evidence_id=record.id,
                status=outcomes[record.id].status,
                requirement_id=outcomes[record.id].requirement_id,
                alternative_requirement_ids=outcomes[record.id].alternative_requirement_ids,
                reason=outcomes[record.id].reason,
                mapping=mappings.get(record.id),
            )
            for record in package.evidence
        )

    def decide(
        self,
        package: ProductKnowledgePackage,
        mapping_result: MappingResult,
        template_index: TemplateIndex,
        item: SemanticReviewItem,
        *,
        decision: ReviewDecision,
        thread_id: str,
        corrected_requirement_id: str | None = None,
        corrected_value: str | None = None,
        comment: str | None = None,
        actor_name: str | None = None,
    ) -> tuple[ProductKnowledgePackage, MappingResult, SemanticReviewItem]:
        """Apply one row decision while preserving a single outcome per evidence ID."""

        normalized = {"approve": "keep", "correct": "change_target"}.get(decision, decision)
        evidence_id = item.evidence_id
        mapping = item.mapping
        if normalized == "keep":
            if item.status is EvidenceOutcomeStatus.UNCERTAIN:
                raise ValueError("uncertain evidence requires an explicit target or disposition")
            if mapping is not None:
                mapping = mapping.model_copy(
                    update={
                        "status": (
                            MappingStatus.AUTO
                            if mapping.mapping_origin is MappingOrigin.DETERMINISTIC
                            else MappingStatus.APPROVED
                        ),
                        "human_reviewed": True,
                        "human_actor_name": actor_name,
                        "human_value_kind": mapping.human_value_kind,
                        "human_comment": comment,
                    }
                )
            reviewed = item.model_copy(update={"mapping": mapping})
            return package, self._replace(package, mapping_result, reviewed), reviewed

        if normalized in {"unmapped", "irrelevant", "reject"}:
            status = {
                "unmapped": EvidenceOutcomeStatus.UNMAPPED,
                "irrelevant": EvidenceOutcomeStatus.IRRELEVANT,
                "reject": EvidenceOutcomeStatus.REJECTED,
            }[normalized]
            if normalized == "reject":
                package = package.model_copy(
                    update={
                        "evidence": tuple(
                            record.model_copy(update={"status": EvidenceStatus.REJECTED})
                            if record.id == evidence_id
                            else record
                            for record in package.evidence
                        )
                    }
                )
            reviewed = item.model_copy(
                update={
                    "status": status,
                    "requirement_id": None,
                    "alternative_requirement_ids": (),
                    "reason": comment or f"Human marked this evidence {status.value}.",
                    "mapping": None,
                }
            )
            return package, self._replace(package, mapping_result, reviewed), reviewed

        requirement_id = corrected_requirement_id or item.requirement_id
        requirement = self._fixed_requirement(template_index, requirement_id)
        record = next(record for record in package.evidence if record.id == evidence_id)
        corrected = False
        if corrected_value is not None and corrected_value.strip() != str(record.value):
            corrected = True
            original = record
            record = self._human_evidence(
                record,
                corrected_value.strip(),
                thread_id,
                actor_name=actor_name,
            )
            package = package.model_copy(
                update={
                    "evidence": (
                        *(
                            item.model_copy(update={"status": EvidenceStatus.REJECTED})
                            if item.id == original.id
                            else item
                            for item in package.evidence
                        ),
                        record,
                    )
                }
            )
        mapping = self._human_mapping(
            record,
            requirement,
            comment,
            actor_name=actor_name,
            human_value_kind="verified",
        )
        reviewed = item.model_copy(
            update={
                "evidence_id": record.id,
                "status": EvidenceOutcomeStatus.MAPPED,
                "requirement_id": requirement.id,
                "alternative_requirement_ids": (),
                "reason": "Human corrected and confirmed the official target.",
                "mapping": mapping,
            }
        )
        return (
            package,
            self._replace(
                package,
                mapping_result,
                reviewed,
                replaced_id=evidence_id,
                retain_replaced_as_rejected=corrected,
            ),
            reviewed,
        )

    def record_human_value(
        self,
        package: ProductKnowledgePackage,
        mapping_result: MappingResult,
        template_index: TemplateIndex,
        *,
        requirement_id: str,
        value: str,
        thread_id: str,
        actor_name: str | None = None,
        use_dummy: bool = False,
    ) -> tuple[ProductKnowledgePackage, MappingResult]:
        requirement = self._fixed_requirement(template_index, requirement_id)
        cleaned = self._dummy_value(requirement) if use_dummy else value.strip()
        if not cleaned:
            raise ValueError("human evidence value must not be empty")
        report = coverage(package, template_index, mapping_result=mapping_result)
        current = next(item for item in report.coverage if item.requirement_id == requirement_id)
        if current.status.value == "satisfied":
            raise ValueError("the requirement is already satisfied")
        evidence = self._human_requirement_evidence(
            requirement,
            cleaned,
            thread_id,
            is_dummy=use_dummy,
            actor_name=actor_name,
        )
        package = package.model_copy(update={"evidence": (*package.evidence, evidence)})
        mapping = self._human_mapping(
            evidence,
            requirement,
            None,
            actor_name=actor_name,
            human_value_kind="dummy" if use_dummy else "verified",
        )
        outcome = EvidenceOutcome(
            evidence_id=evidence.id,
            status=EvidenceOutcomeStatus.MAPPED,
            requirement_id=requirement.id,
            reason=(
                "A trusted human supplied an explicit type-compatible DUMMY placeholder."
                if use_dummy
                else "A trusted human supplied this missing official value."
            ),
            mapping_origin=MappingOrigin.HUMAN,
        )
        result = mapping_result.model_copy(
            update={
                "mapped": (*mapping_result.mapped, mapping),
                "outcomes": (*mapping_result.outcomes, outcome),
            }
        )
        self.validate_complete_accounting(package, result)
        return package, result

    def _replace(
        self,
        package: ProductKnowledgePackage,
        mapping_result: MappingResult,
        reviewed: SemanticReviewItem,
        *,
        replaced_id: str | None = None,
        retain_replaced_as_rejected: bool = False,
    ) -> MappingResult:
        old_id = replaced_id or reviewed.evidence_id
        retained = [item for item in mapping_result.mapped if item.evidence_id != old_id]
        ambiguous = [item for item in mapping_result.ambiguous if item.evidence_id != old_id]
        rejected = [item for item in mapping_result.rejected if item.evidence_id != old_id]
        unmatched = [item for item in mapping_result.unmatched_evidence_ids if item != old_id]
        irrelevant = [item for item in mapping_result.irrelevant_evidence_ids if item != old_id]
        rejected_ids = [item for item in mapping_result.rejected_evidence_ids if item != old_id]
        outcomes = [item for item in mapping_result.outcomes if item.evidence_id != old_id]

        if reviewed.status is EvidenceOutcomeStatus.MAPPED and reviewed.mapping is not None:
            retained.append(reviewed.mapping)
        elif reviewed.status is EvidenceOutcomeStatus.UNCERTAIN and reviewed.mapping is not None:
            ambiguous.append(reviewed.mapping)
        elif reviewed.status is EvidenceOutcomeStatus.UNMAPPED:
            unmatched.append(reviewed.evidence_id)
        elif reviewed.status is EvidenceOutcomeStatus.IRRELEVANT:
            irrelevant.append(reviewed.evidence_id)
        elif reviewed.status is EvidenceOutcomeStatus.REJECTED:
            rejected_ids.append(reviewed.evidence_id)

        outcomes.append(
            EvidenceOutcome(
                evidence_id=reviewed.evidence_id,
                status=reviewed.status,
                requirement_id=reviewed.requirement_id,
                alternative_requirement_ids=reviewed.alternative_requirement_ids,
                reason=reviewed.reason,
                mapping_origin=(
                    reviewed.mapping.mapping_origin
                    if reviewed.mapping is not None
                    else MappingOrigin.HUMAN
                ),
            )
        )
        if retain_replaced_as_rejected:
            rejected_ids.append(old_id)
            outcomes.append(
                EvidenceOutcome(
                    evidence_id=old_id,
                    status=EvidenceOutcomeStatus.REJECTED,
                    reason="Human supplied a supported correction for this source value.",
                    mapping_origin=MappingOrigin.HUMAN,
                )
            )
        result = MappingResult(
            mapped=tuple(retained),
            ambiguous=tuple(ambiguous),
            rejected=tuple(rejected),
            unmatched_evidence_ids=tuple(unmatched),
            irrelevant_evidence_ids=tuple(irrelevant),
            rejected_evidence_ids=tuple(rejected_ids),
            outcomes=tuple(outcomes),
        )
        self.validate_complete_accounting(package, result)
        return result

    def _semantic_mapping(
        self,
        evidence: EvidenceRecord,
        requirement: Requirement,
        reason: str,
        *,
        alternatives: tuple[str, ...],
    ) -> FieldMapping:
        target = mapping_target(
            self._repository.load(requirement.template_key), requirement.template_path
        )
        identity = f"{requirement.id}\0{evidence.id}"
        assessment = MappingAssessment(
            basis=MappingBasis.SEMANTIC,
            review_required=True,
            reason="The batch semantic mapper proposed an official target.",
            uncertainties=("Human confirmation is required.",),
        )
        return FieldMapping(
            id="mapping-" + hashlib.sha256(identity.encode()).hexdigest()[:24],
            evidence_id=evidence.id,
            source_field=evidence.source_label or evidence.predicate,
            source_value=self._display_value(evidence),
            target=target,
            assessment=assessment,
            reasoning=reason,
            status=MappingStatus.REVIEW,
            mapping_origin=MappingOrigin.SEMANTIC_AGENT,
            llm_review=LlmReview(
                conclusion=f"Proposed {target.id_short} for this source fact.",
                rationale=reason,
                evidence_ids=(evidence.id,),
                alternative_target_ids=alternatives,
                uncertainties=assessment.uncertainties,
            ),
        )

    def _human_mapping(
        self,
        evidence: EvidenceRecord,
        requirement: Requirement,
        comment: str | None,
        *,
        actor_name: str | None,
        human_value_kind: Literal["verified", "dummy"],
    ) -> FieldMapping:
        target = mapping_target(
            self._repository.load(requirement.template_key), requirement.template_path
        )
        identity = f"{requirement.id}\0{evidence.id}"
        return FieldMapping(
            id="mapping-" + hashlib.sha256(identity.encode()).hexdigest()[:24],
            evidence_id=evidence.id,
            source_field=evidence.source_label or evidence.predicate,
            source_value=self._display_value(evidence),
            target=target,
            assessment=MappingAssessment(
                basis=MappingBasis.HUMAN,
                review_required=False,
                reason="A trusted human confirmed this official target.",
            ),
            reasoning="Validated and accepted through human mapping review.",
            status=MappingStatus.APPROVED,
            mapping_origin=MappingOrigin.HUMAN,
            human_reviewed=True,
            human_actor_name=actor_name,
            human_value_kind=human_value_kind,
            human_comment=comment,
        )

    @staticmethod
    def _fixed_requirement(
        template_index: TemplateIndex,
        requirement_id: str | None,
    ) -> Requirement:
        requirement = next(
            (item for item in template_index.requirements if item.id == requirement_id), None
        )
        if (
            requirement is None
            or requirement.semantic_id is None
            or requirement.wildcard
            or requirement.kind is not RequirementKind.VALUE
        ):
            raise ValueError("review target must be a fixed official value requirement")
        return requirement

    @staticmethod
    def _requirement_id(mapping: FieldMapping, index: TemplateIndex) -> str:
        requirement = next(
            (
                item
                for item in index.requirements
                if item.template_key == mapping.target.template_key
                and item.template_release == mapping.target.template_release
                and item.template_path == mapping.target.template_path
            ),
            None,
        )
        if requirement is None:
            raise ValueError("deterministic mapping target is not in the selected template index")
        return requirement.id

    @staticmethod
    def _human_evidence(
        original: EvidenceRecord,
        value: str,
        thread_id: str,
        *,
        actor_name: str | None,
    ) -> EvidenceRecord:
        acquired_at = datetime.now(UTC)
        identity = f"{thread_id}\0{original.id}\0{value}\0{acquired_at.isoformat()}"
        return EvidenceRecord(
            id="ev-human-" + hashlib.sha256(identity.encode()).hexdigest()[:24],
            predicate="human.correction",
            source_label=original.source_label,
            value=value,
            unit=original.unit,
            context_path=original.context_path,
            source_type=SourceType.HUMAN,
            human_actor_name=actor_name,
            human_value_kind="verified",
            human_reason="Human corrected a source-derived value during mapping review.",
            source_uri=f"mia://conversation/{thread_id}/review",
            source_content_sha256=hashlib.sha256(value.encode()).hexdigest(),
            source_location=SourceLocation(excerpt=value),
            extraction_method="human_review_correction",
            extractor_name="mia-agent",
            extractor_version="3",
            status=EvidenceStatus.VERIFIED,
            acquired_at=acquired_at,
        )

    @staticmethod
    def _human_requirement_evidence(
        requirement: Requirement,
        value: str,
        thread_id: str,
        *,
        is_dummy: bool,
        actor_name: str | None,
    ) -> EvidenceRecord:
        acquired_at = datetime.now(UTC)
        identity = f"{thread_id}\0{requirement.id}\0{value}\0{acquired_at.isoformat()}"
        return EvidenceRecord(
            id="ev-human-" + hashlib.sha256(identity.encode()).hexdigest()[:24],
            predicate="human.dummy" if is_dummy else "human.answer",
            source_label=requirement.id_short or requirement.template_path[-1],
            value=value,
            source_type=SourceType.HUMAN,
            human_actor_name=actor_name,
            human_value_kind="dummy" if is_dummy else "verified",
            human_reason=(
                "Human approved a placeholder because the mandatory value was unavailable."
                if is_dummy
                else "Human supplied a missing mandatory value."
            ),
            source_uri=f"mia://conversation/{thread_id}/requirement/{requirement.id}",
            source_content_sha256=hashlib.sha256(value.encode()).hexdigest(),
            source_location=SourceLocation(excerpt=value),
            extraction_method=(
                "human_dummy_requirement_answer" if is_dummy else "human_requirement_answer"
            ),
            extractor_name="mia-agent",
            extractor_version="3",
            status=EvidenceStatus.VERIFIED,
            acquired_at=acquired_at,
        )

    @staticmethod
    def _dummy_value(requirement: Requirement) -> str:
        if requirement.allowed_values:
            return requirement.allowed_values[0]
        value_type = (requirement.value_type or "xs:string").casefold()
        if "bool" in value_type:
            return "false"
        if any(token in value_type for token in ("int", "integer", "long", "short", "byte")):
            return "0"
        if any(token in value_type for token in ("decimal", "double", "float")):
            return "0"
        if "datetime" in value_type:
            return "1970-01-01T00:00:00Z"
        if value_type.endswith("date") or ":date" in value_type:
            return "1970-01-01"
        if value_type.endswith("time") or ":time" in value_type:
            return "00:00:00Z"
        return "DUMMY"

    @staticmethod
    def _display_value(evidence: EvidenceRecord) -> str:
        text = str(evidence.value)
        if evidence.unit and not text.endswith(evidence.unit):
            return f"{text} {evidence.unit}"
        return text

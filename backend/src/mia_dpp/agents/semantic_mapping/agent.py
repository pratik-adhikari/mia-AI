"""One complete typed semantic mapping operation per evidence cycle."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, cast

from pydantic import Field, create_model
from pydantic_ai import Agent
from pydantic_ai.models import Model

from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.mappings import MappingResult
from mia_dpp.domain.targets import RequirementKind, TemplateIndex
from mia_dpp.tools.mapping.models import (
    BatchSemanticMappingResult,
    DeterministicMappingHint,
    EvidenceMappingDecision,
    LeanEvidence,
    LeanTarget,
    SemanticMappingMetrics,
    SemanticMappingRun,
)

SEMANTIC_MAPPING_INSTRUCTIONS = """You map extracted manufacturer product facts to official
AAS/IDTA template fields. Consider the meaning of the source label, value, unit, source hierarchy,
target name, target description, semantic identifiers, expected datatype/unit, and target path.
Do not map merely because names contain similar words. Do not invent targets. If no suitable
official target exists, mark the evidence unmapped. If more than one target is genuinely plausible,
mark it uncertain and provide alternatives. Preserve component context: identical labels under
different components are different facts. Existing deterministic mappings are authoritative and
must be returned unchanged. Copy every evidence_id and requirement_id character-for-character
from the supplied allowed-ID lists; never transcribe, shorten, repair, or generate an ID. Return
exactly one concise decision for every evidence record, in the same order as requiredEvidenceIds.
Use status mapped only with a requirement_id. When no target matches, use status unmapped, never
mapped. Unmapped and irrelevant decisions must not contain requirement IDs or alternatives.
The reason is a short mapping justification, not hidden chain-of-thought.
"""


def lean_evidence(package: ProductKnowledgePackage) -> tuple[LeanEvidence, ...]:
    """Project every canonical evidence record without provenance-only metadata."""

    return tuple(
        LeanEvidence(
            id=item.id,
            label=item.source_label or item.predicate,
            value=str(item.value),
            unit=item.unit,
            context=item.context_path,
        )
        for item in package.evidence
    )


def lean_targets(template_index: TemplateIndex) -> tuple[LeanTarget, ...]:
    """Project every fixed value target from the selected official templates."""

    return tuple(
        LeanTarget(
            id=item.id,
            template=item.template_key,
            name=item.id_short or item.template_path[-1],
            path=item.template_path,
            description=item.description,
            semantic_id=item.semantic_id.primary_value if item.semantic_id else None,
            supplemental_semantic_ids=tuple(
                reference.primary_value for reference in item.supplemental_semantic_ids
            ),
            model_type=item.model_type,
            value_type=item.value_type,
            unit=item.unit,
            allowed_values=item.allowed_values,
            required=item.required,
            conditional=item.conditional,
            wildcard=item.wildcard,
        )
        for item in template_index.requirements
        if item.kind is RequirementKind.VALUE and item.semantic_id is not None and not item.wildcard
    )


def deterministic_hints(
    mapping_result: MappingResult,
    template_index: TemplateIndex,
) -> tuple[DeterministicMappingHint, ...]:
    by_target = {
        (item.template_key, item.template_release, item.template_path): item.id
        for item in template_index.requirements
    }
    return tuple(
        DeterministicMappingHint(
            evidence_id=mapping.evidence_id,
            requirement_id=by_target[
                (
                    mapping.target.template_key,
                    mapping.target.template_release,
                    mapping.target.template_path,
                )
            ],
        )
        for mapping in mapping_result.mapped
        if (
            mapping.target.template_key,
            mapping.target.template_release,
            mapping.target.template_path,
        )
        in by_target
    )


def validate_batch_result(
    result: BatchSemanticMappingResult,
    evidence: tuple[LeanEvidence, ...],
    targets: tuple[LeanTarget, ...],
    hints: tuple[DeterministicMappingHint, ...] = (),
) -> None:
    """Reject the complete model result before any trusted mapping state changes."""

    expected_evidence = {item.id for item in evidence}
    returned_ids = [item.evidence_id for item in result.decisions]
    if len(returned_ids) != len(set(returned_ids)):
        raise ValueError("semantic mapping returned duplicate evidence IDs")
    returned = set(returned_ids)
    unknown = returned - expected_evidence
    missing = expected_evidence - returned
    if unknown:
        raise ValueError(f"semantic mapping returned unknown evidence IDs: {sorted(unknown)}")
    if missing:
        raise ValueError(f"semantic mapping omitted evidence IDs: {sorted(missing)}")

    target_ids = {item.id for item in targets}
    for decision in result.decisions:
        supplied = ({decision.requirement_id} if decision.requirement_id else set()) | set(
            decision.alternative_requirement_ids
        )
        invalid = supplied - target_ids
        if invalid:
            raise ValueError(f"semantic mapping invented requirement IDs: {sorted(invalid)}")

    decisions = {item.evidence_id: item for item in result.decisions}
    for hint in hints:
        decision = decisions[hint.evidence_id]
        if decision.status != "mapped" or decision.requirement_id != hint.requirement_id:
            raise ValueError("semantic mapping contradicted an authoritative deterministic mapping")


def constrained_batch_output_type(
    evidence: tuple[LeanEvidence, ...],
    targets: tuple[LeanTarget, ...],
) -> type[BatchSemanticMappingResult]:
    """Build a structured-output schema containing only IDs valid for this cycle."""

    evidence_ids = tuple(item.id for item in evidence)
    target_ids = tuple(item.id for item in targets)
    if not evidence_ids:
        raise ValueError("semantic mapping requires at least one evidence record")

    evidence_id_type: Any = Literal.__getitem__(evidence_ids)
    no_target_type = create_model(
        "NoTargetEvidenceMappingDecision",
        __base__=EvidenceMappingDecision,
        evidence_id=(evidence_id_type, ...),
        status=(Literal["unmapped", "irrelevant"], ...),
        requirement_id=(Literal[None], None),
        alternative_requirement_ids=(tuple[()], ()),
    )
    if target_ids:
        requirement_id_type: Any = Literal.__getitem__(target_ids)
        mapped_type = create_model(
            "MappedEvidenceMappingDecision",
            __base__=EvidenceMappingDecision,
            evidence_id=(evidence_id_type, ...),
            status=(Literal["mapped"], ...),
            requirement_id=(requirement_id_type, ...),
            alternative_requirement_ids=(tuple[requirement_id_type, ...], ()),
        )
        uncertain_type = create_model(
            "UncertainEvidenceMappingDecision",
            __base__=EvidenceMappingDecision,
            evidence_id=(evidence_id_type, ...),
            status=(Literal["uncertain"], ...),
            requirement_id=(requirement_id_type | None, None),
            alternative_requirement_ids=(tuple[requirement_id_type, ...], ()),
        )
        decision_field_type: Any = Annotated[
            mapped_type | uncertain_type | no_target_type,
            Field(discriminator="status"),
        ]
    else:
        decision_field_type = no_target_type
    result_type = create_model(
        "ConstrainedBatchSemanticMappingResult",
        __base__=BatchSemanticMappingResult,
        decisions=(tuple[decision_field_type, ...], ...),
    )
    return cast(type[BatchSemanticMappingResult], result_type)


class PydanticBatchSemanticMapper:
    """Dedicated PydanticAI structured-output mapper invoked once per cycle."""

    def __init__(self, model: Model) -> None:
        self._agent: Agent[None, BatchSemanticMappingResult] = Agent(
            model,
            name="mia-semantic-mapper",
            output_type=BatchSemanticMappingResult,
            instructions=SEMANTIC_MAPPING_INSTRUCTIONS,
            retries=0,
        )

    async def map(
        self,
        package: ProductKnowledgePackage,
        template_index: TemplateIndex,
        deterministic: MappingResult,
        *,
        reviewed_knowledge: tuple[dict[str, object], ...] = (),
    ) -> SemanticMappingRun:
        evidence = lean_evidence(package)
        targets = lean_targets(template_index)
        hints = deterministic_hints(deterministic, template_index)
        evidence_json = json.dumps(
            [item.model_dump(mode="json") for item in evidence],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        target_json = json.dumps(
            [item.model_dump(mode="json") for item in targets],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        prompt = json.dumps(
            {
                "requiredEvidenceIds": [item.id for item in evidence],
                "allowedRequirementIds": [item.id for item in targets],
                "evidence": json.loads(evidence_json),
                "targets": json.loads(target_json),
                "authoritativeDeterministicMappings": [
                    item.model_dump(mode="json") for item in hints
                ],
                "trustedReviewedMappingKnowledge": reviewed_knowledge,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        output_type = constrained_batch_output_type(evidence, targets)
        response = await self._agent.run(prompt, output_type=cast(Any, output_type))
        usage = response.usage
        if usage.requests != 1:
            raise RuntimeError(
                f"semantic mapping used {usage.requests} model requests; exactly one is required"
            )
        result = BatchSemanticMappingResult.model_validate(
            response.output.model_dump(mode="json")
        )
        validate_batch_result(result, evidence, targets, hints)
        return SemanticMappingRun(
            evidence=evidence,
            targets=targets,
            deterministic_mappings=hints,
            result=result,
            metrics=SemanticMappingMetrics(
                evidence_count=len(evidence),
                target_count=len(targets),
                lean_evidence_json_bytes=len(evidence_json.encode()),
                lean_target_json_bytes=len(target_json.encode()),
                model_requests=usage.requests,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
            ),
        )

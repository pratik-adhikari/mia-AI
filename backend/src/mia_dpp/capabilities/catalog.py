"""Machine-readable capability metadata for future pipeline builders and evaluators."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ExecutionRequirement(StrEnum):
    DETERMINISTIC = "deterministic"
    LOCAL_MODEL_OK = "local_model_ok"
    REMOTE_MODEL_OPTIONAL = "remote_model_optional"
    REMOTE_MODEL_REQUIRED = "remote_model_required"


@dataclass(frozen=True, slots=True)
class ComponentSpec:
    id: str
    title: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    execution: ExecutionRequirement
    side_effects: bool = False


DEFAULT_COMPONENTS: tuple[ComponentSpec, ...] = (
    ComponentSpec(
        id="extract.web",
        title="Web evidence extraction",
        inputs=("product_url",),
        outputs=("product_knowledge_package",),
        execution=ExecutionRequirement.LOCAL_MODEL_OK,
        side_effects=True,
    ),
    ComponentSpec(
        id="normalize.evidence",
        title="Evidence normalization",
        inputs=("product_knowledge_package",),
        outputs=("normalization_report",),
        execution=ExecutionRequirement.DETERMINISTIC,
    ),
    ComponentSpec(
        id="semantic.context",
        title="Semantic context construction",
        inputs=("product_knowledge_package", "normalization_report"),
        outputs=("context_views",),
        execution=ExecutionRequirement.DETERMINISTIC,
    ),
    ComponentSpec(
        id="semantic.jev_route",
        title="Bounded Jev semantic routing",
        inputs=("product_knowledge_package", "context_views", "template_index"),
        outputs=("routing_report",),
        execution=ExecutionRequirement.REMOTE_MODEL_OPTIONAL,
    ),
    ComponentSpec(
        id="semantic.eclass",
        title="Authoritative ECLASS resolution",
        inputs=("routing_report",),
        outputs=("eclass_resolution",),
        execution=ExecutionRequirement.REMOTE_MODEL_OPTIONAL,
    ),
    ComponentSpec(
        id="mapping.resolve",
        title="Evidence-to-template mapping",
        inputs=("product_knowledge_package", "template_index"),
        outputs=("mapping_result",),
        execution=ExecutionRequirement.LOCAL_MODEL_OK,
    ),
    ComponentSpec(
        id="coverage.calculate",
        title="Coverage calculation",
        inputs=("product_knowledge_package", "template_index", "mapping_result"),
        outputs=("coverage_report",),
        execution=ExecutionRequirement.DETERMINISTIC,
    ),
    ComponentSpec(
        id="research.gaps",
        title="Gap-driven research",
        inputs=("coverage_report",),
        outputs=("product_knowledge_package",),
        execution=ExecutionRequirement.LOCAL_MODEL_OK,
        side_effects=True,
    ),
    ComponentSpec(
        id="aas.build",
        title="Validated AAS/DPP build",
        inputs=("product_knowledge_package", "mapping_result"),
        outputs=("dpp_package", "validation_report"),
        execution=ExecutionRequirement.DETERMINISTIC,
    ),
)


def component_catalog() -> dict[str, ComponentSpec]:
    return {component.id: component for component in DEFAULT_COMPONENTS}

"""Compatibility imports for the semantic-mapping agent.

New code should import from :mod:`mia_dpp.agents.semantic_mapping.agent`.
"""

from mia_dpp.agents.semantic_mapping.agent import (
    PydanticBatchSemanticMapper,
    constrained_batch_output_type,
    deterministic_hints,
    lean_evidence,
    lean_targets,
    validate_batch_result,
)

__all__ = [
    "PydanticBatchSemanticMapper",
    "constrained_batch_output_type",
    "deterministic_hints",
    "lean_evidence",
    "lean_targets",
    "validate_batch_result",
]

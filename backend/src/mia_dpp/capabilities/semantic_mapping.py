"""Shared semantic-mapping execution independent of orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.mappings import MappingResult, SemanticReviewItem
from mia_dpp.domain.targets import TemplateIndex
from mia_dpp.tools.mapping.models import SemanticMapper, SemanticMappingRun
from mia_dpp.tools.mapping.review import MappingReviewService


@dataclass(frozen=True, slots=True)
class SemanticMappingExecution:
    """One model mapping run plus its trusted projection and review rows."""

    semantic_run: SemanticMappingRun
    mapping: MappingResult
    reviews: tuple[SemanticReviewItem, ...]


async def run_semantic_mapping(
    package: ProductKnowledgePackage,
    index: TemplateIndex,
    deterministic: MappingResult,
    *,
    mapper: SemanticMapper,
    review: MappingReviewService,
    reviewed_knowledge: tuple[dict[str, object], ...] = (),
) -> SemanticMappingExecution:
    """Execute a semantic mapper once and apply the same trust/review rules everywhere."""

    semantic_run = await mapper.map(
        package,
        index,
        deterministic,
        reviewed_knowledge=reviewed_knowledge,
    )
    mapping = review.apply_semantic_run(
        package,
        deterministic,
        index,
        semantic_run,
    )
    reviews = review.complete_review(package, mapping, index)
    return SemanticMappingExecution(
        semantic_run=semantic_run,
        mapping=mapping,
        reviews=reviews,
    )

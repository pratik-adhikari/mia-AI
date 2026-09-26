"""Reusable deterministic AAS/DPP compilation independent of orchestration."""

from mia_dpp.aas.build import build_dpp
from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.mappings import MappingResult, MappingStatus


def build_validated_dpp(
    package: ProductKnowledgePackage,
    mapping: MappingResult,
    *,
    templates: OfficialTemplateRepository,
):
    """Compile accepted mappings into the validated DPP/AAS package."""

    accepted = tuple(
        item
        for item in mapping.mapped
        if item.status in {MappingStatus.AUTO, MappingStatus.APPROVED}
    )
    return build_dpp(
        package.product_name,
        accepted,
        repository=templates,
        evidence=package.evidence,
    )

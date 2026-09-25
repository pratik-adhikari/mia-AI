"""Construct typed mapping targets from authoritative template metadata."""

from mia_dpp.aas.templates import resolve_element
from mia_dpp.domain.mappings import ListInstanceBinding, MappingTarget
from mia_dpp.domain.targets import ReferenceKey, SemanticReference, SubmodelTemplate
from mia_dpp.errors import MappingError

NAMEPLATE_ROOT = "Nameplate"
ARBITRARY_PROPERTY_PATH = (
    NAMEPLATE_ROOT,
    "AssetSpecificProperties",
    "ArbitraryProperty",
)
TECHNICAL_PROPERTY_AREA_LIST_PATH = (
    "TechnicalData",
    "TechnicalPropertyAreas",
    "[]",
)
TECHNICAL_DATA_ARBITRARY_PROPERTY_PATH = (
    *TECHNICAL_PROPERTY_AREA_LIST_PATH,
    "ArbitraryProperty",
)


def external_reference(value: str) -> SemanticReference:
    return SemanticReference(
        type="ExternalReference",
        keys=(ReferenceKey(type="GlobalReference", value=value),),
    )


def mapping_target(
    template: SubmodelTemplate,
    path: tuple[str, ...],
    *,
    id_short: str | None = None,
    semantic_id: str | None = None,
    list_instance_bindings: tuple[ListInstanceBinding, ...] = (),
) -> MappingTarget:
    """Construct a mapping target from authoritative template metadata.

    Mapping and review code call this before accepting a target. Fixed targets
    cannot be overridden; wildcard targets require an explicit semantic ID.
    """

    element = resolve_element(template, path)
    target_id_short = id_short or element.id_short
    if target_id_short is None:
        raise MappingError(f"template path {'/'.join(path)} has no target idShort")
    if element.wildcard:
        if semantic_id is None:
            raise MappingError("wildcard target requires an explicit semantic ID")
        reference = external_reference(semantic_id)
        instance_path = (*element.path[:-1], target_id_short)
    else:
        if element.semantic_id is None:
            raise MappingError(f"template path {'/'.join(path)} has no semantic ID")
        if id_short is not None or semantic_id is not None:
            raise MappingError("fixed official targets cannot be overridden")
        reference = element.semantic_id
        instance_path = element.path
    return MappingTarget(
        template_key=template.release.key,
        template_release=template.release.release,
        template_path=element.path,
        instance_path=instance_path,
        id_short=target_id_short,
        semantic_id=reference,
        list_instance_bindings=list_instance_bindings,
    )

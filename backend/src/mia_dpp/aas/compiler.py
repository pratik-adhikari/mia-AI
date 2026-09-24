"""Deterministic projection of approved evidence onto official AAS templates."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from importlib.metadata import version
from typing import Any, cast

from aas_core3 import jsonization

from mia_dpp.aas._structure import _children
from mia_dpp.aas.identifiers import ID_SHORT_PATTERN, sanitize_id_short
from mia_dpp.aas.models import AasArtifact
from mia_dpp.aas.templates import OfficialTemplateRepository, resolve_element
from mia_dpp.canonical import sha256_json
from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.mappings import FieldMapping, MappingTarget
from mia_dpp.domain.targets import SemanticReference, SubmodelTemplate
from mia_dpp.errors import CompilationError, MappingError

_VALUE_MODEL_TYPES = {"Property", "MultiLanguageProperty", "Range", "File", "Blob"}
_CONTAINER_MODEL_TYPES = {"SubmodelElementCollection", "SubmodelElementList", "Entity"}


def _reference_json(reference: SemanticReference) -> dict[str, Any]:
    return reference.model_dump(mode="json", by_alias=True)


def _child_key(model_type: str) -> str | None:
    return {
        "Submodel": "submodelElements",
        "SubmodelElementCollection": "value",
        "SubmodelElementList": "value",
        "Entity": "statements",
        "AnnotatedRelationshipElement": "annotations",
    }.get(model_type)


def _template_segment(raw: Mapping[str, Any], parent_model_type: str) -> str:
    if parent_model_type == "SubmodelElementList":
        return "[]"
    value = raw.get("idShort")
    if not isinstance(value, str) or not value:
        raise CompilationError("official template element has no usable idShort")
    return value


def _metadata(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Copy instance-relevant template metadata, excluding examples and qualifiers."""

    result: dict[str, Any] = {}
    for key in (
        "category",
        "idShort",
        "semanticId",
        "supplementalSemanticIds",
        "modelType",
        "valueType",
        "contentType",
        "orderRelevant",
        "typeValueListElement",
        "valueTypeListElement",
        "semanticIdListElement",
    ):
        if key in raw:
            result[key] = copy.deepcopy(raw[key])
    if result.get("idShort") == "":
        result.pop("idShort", None)
    return result


def _set_leaf_value(raw: dict[str, Any], mapping: FieldMapping, value: str) -> None:
    target = mapping.target
    raw["idShort"] = target.id_short
    raw["semanticId"] = _reference_json(target.semantic_id)
    model_type = raw.get("modelType")
    if model_type == "Property":
        raw["value"] = value
    elif model_type == "MultiLanguageProperty":
        raw["value"] = [{"language": "en", "text": value}]
    elif model_type == "Range":
        parts = re.split(r"\s*(?:-|\u2013|to|bis)\s*", value, maxsplit=1)
        if len(parts) != 2:
            raise CompilationError(f"range value {value!r} has no deterministic bounds")
        raw["min"], raw["max"] = parts
    elif model_type == "File":
        raw["value"] = value
    elif model_type == "Blob":
        raw["value"] = value
    else:
        raise CompilationError(f"unsupported mapped model type: {model_type}")


class AasCompiler:
    """Project approved evidence onto official template nodes.

    ``build_dpp`` calls this after review. It verifies every target against the
    selected template and serializes the result with aas-core.
    """

    def __init__(self, repository: OfficialTemplateRepository) -> None:
        self._repository = repository

    def compile(
        self,
        package: ProductKnowledgePackage,
        mappings: Sequence[FieldMapping],
        template: SubmodelTemplate,
    ) -> AasArtifact:
        """Build one stable AAS artifact from accepted mappings."""

        identities = [item.target.projection_identity for item in mappings]
        if len(identities) != len(set(identities)):
            raise MappingError("mapping projection identities must be unique")
        evidence = {item.id: item for item in package.evidence}
        resolved: list[tuple[FieldMapping, str]] = []
        for approved in mappings:
            if approved.evidence_id not in evidence:
                raise MappingError(f"mapping references unknown evidence {approved.evidence_id!r}")
            value = evidence[approved.evidence_id].value
            if not isinstance(value, (str, int, float, bool)):
                raise CompilationError("AAS scalar targets require scalar evidence values")
            self._validate_target(approved.target, template)
            resolved.append((approved, str(value)))

        stable_seed = {
            "productId": package.product_id,
            "productName": package.product_name,
            "template": template.release.model_dump(mode="json"),
            "mappings": [
                {
                    "evidence": approved.evidence_id,
                    "path": approved.target.instance_path,
                    "listInstances": [
                        {
                            "path": binding.template_path,
                            "key": binding.instance_key,
                        }
                        for binding in approved.target.list_instance_bindings
                    ],
                    "value": value,
                }
                for approved, value in sorted(
                    resolved,
                    key=lambda item: item[0].target.projection_identity,
                )
            ],
        }
        suffix = sha256_json(stable_seed)[:32]
        aas_id = f"urn:mia:aas:{suffix}"
        asset_id = f"urn:mia:asset:{suffix}"
        submodel_id = f"urn:mia:submodel:{template.release.key}:{suffix}"

        raw_submodel = self._repository.raw_submodel(template.release.key)
        raw_elements = raw_submodel.get("submodelElements")
        if not isinstance(raw_elements, list):
            raise CompilationError("official template submodelElements is not a list")
        ordered_mappings = sorted(
            (approved for approved, _ in resolved),
            key=lambda item: item.target.projection_identity,
        )
        values = {approved.evidence_id: value for approved, value in resolved}
        projected = self._project_children(
            raw_elements,
            parent_path=(template.id_short,),
            parent_model_type="Submodel",
            mappings=ordered_mappings,
            values=values,
        )
        projected = self._add_system_elements(projected, raw_elements, template, asset_id)

        submodel: dict[str, Any] = {
            "id": submodel_id,
            "idShort": template.id_short,
            "kind": "Instance",
            "semanticId": _reference_json(template.semantic_id),
            "submodelElements": projected,
            "modelType": "Submodel",
        }
        shell: dict[str, Any] = {
            "id": aas_id,
            "idShort": sanitize_id_short(package.product_name),
            "assetInformation": {
                "assetKind": "Instance",
                "globalAssetId": asset_id,
            },
            "submodels": [
                {
                    "type": "ModelReference",
                    "keys": [{"type": "Submodel", "value": submodel_id}],
                }
            ],
            "modelType": "AssetAdministrationShell",
        }
        document = {
            "assetAdministrationShells": [shell],
            "submodels": [submodel],
        }
        try:
            environment = jsonization.environment_from_jsonable(document)
            canonical = cast(dict[str, Any], jsonization.to_jsonable(environment))
        except (TypeError, ValueError) as error:
            raise CompilationError(f"aas-core rejected compiled environment: {error}") from error
        canonical_submodel = cast(dict[str, Any], canonical["submodels"][0])
        return AasArtifact(
            environment=canonical,
            submodel=canonical_submodel,
            sha256=sha256_json(canonical),
            compiler_name="mia-official-template-projector+aas-core3.0",
            compiler_version=version("aas-core3.0"),
        )

    @staticmethod
    def _validate_target(target: MappingTarget, template: SubmodelTemplate) -> None:
        if target.template_key != template.release.key:
            raise MappingError("mapping target belongs to another template")
        if target.template_release != template.release.release:
            raise MappingError("mapping target belongs to another template release")
        official = resolve_element(template, target.template_path)
        if official.model_type not in _VALUE_MODEL_TYPES:
            raise MappingError("mapping target is not a value-bearing template element")
        if not official.wildcard:
            if official.semantic_id is None or target.semantic_id != official.semantic_id:
                raise MappingError("mapping target semantic ID differs from official template")
            if target.instance_path != target.template_path:
                raise MappingError("fixed template target path cannot be changed")
            if target.id_short != official.id_short:
                raise MappingError("fixed template target idShort cannot be changed")
        else:
            if not ID_SHORT_PATTERN.fullmatch(target.id_short):
                raise MappingError("wildcard target idShort is invalid")
            if target.instance_path[:-1] != target.template_path[:-1]:
                raise MappingError("wildcard target parent path cannot be changed")
            if target.instance_path[-1] != target.id_short:
                raise MappingError("wildcard instance path must end with its idShort")

        for binding in target.list_instance_bindings:
            if binding.template_path[-1] != "[]":
                raise MappingError("list-instance binding must end at a [] prototype")
            prototype = resolve_element(template, binding.template_path)
            parent = resolve_element(template, binding.template_path[:-1])
            if parent.model_type != "SubmodelElementList":
                raise MappingError("list-instance binding parent is not a SubmodelElementList")
            if prototype.path != binding.template_path:
                raise MappingError("list-instance binding does not match official template")

    @staticmethod
    def _list_instance_key(
        mapping: FieldMapping,
        prototype_path: tuple[str, ...],
    ) -> str | None:
        matches = tuple(
            binding.instance_key
            for binding in mapping.target.list_instance_bindings
            if binding.template_path == prototype_path
        )
        if len(matches) > 1:
            raise MappingError("mapping has duplicate bindings for one list prototype")
        return matches[0] if matches else None

    def _project_children(
        self,
        raw_children: Sequence[object],
        *,
        parent_path: tuple[str, ...],
        parent_model_type: str,
        mappings: Sequence[FieldMapping],
        values: Mapping[str, str],
    ) -> list[dict[str, Any]]:
        projected: list[dict[str, Any]] = []
        for raw_value in raw_children:
            if not isinstance(raw_value, Mapping):
                continue
            raw = cast(Mapping[str, Any], raw_value)
            segment = _template_segment(raw, parent_model_type)
            path = (*parent_path, segment)
            relevant = [
                mapping for mapping in mappings if mapping.target.template_path[: len(path)] == path
            ]
            if not relevant:
                continue
            exact = [mapping for mapping in relevant if mapping.target.template_path == path]
            model_type = raw.get("modelType")
            if not isinstance(model_type, str):
                continue
            if exact:
                for mapping in exact:
                    instance = _metadata(raw)
                    _set_leaf_value(instance, mapping, values[mapping.evidence_id])
                    if parent_model_type == "SubmodelElementList":
                        instance.pop("idShort", None)
                    projected.append(instance)
                continue
            key = _child_key(model_type)
            if key is None or model_type not in _CONTAINER_MODEL_TYPES:
                raise CompilationError(
                    f"target path {'/'.join(path)!r} crosses unsupported {model_type}"
                )

            if parent_model_type == "SubmodelElementList":
                partitions: dict[str | None, list[FieldMapping]] = {}
                for mapping in relevant:
                    instance_key = self._list_instance_key(mapping, path)
                    partitions.setdefault(instance_key, []).append(mapping)
                for partition in partitions.values():
                    descendants = self._project_children(
                        _children(raw),
                        parent_path=path,
                        parent_model_type=model_type,
                        mappings=partition,
                        values=values,
                    )
                    if not descendants:
                        continue
                    instance = _metadata(raw)
                    instance[key] = descendants
                    instance.pop("idShort", None)
                    projected.append(instance)
                continue

            descendants = self._project_children(
                _children(raw),
                parent_path=path,
                parent_model_type=model_type,
                mappings=relevant,
                values=values,
            )
            if descendants:
                instance = _metadata(raw)
                instance[key] = descendants
                projected.append(instance)
        return projected

    @staticmethod
    def _add_system_elements(
        projected: list[dict[str, Any]],
        raw_elements: Sequence[object],
        template: SubmodelTemplate,
        asset_id: str,
    ) -> list[dict[str, Any]]:
        """Populate structural/identifier requirements without inventing product facts."""

        present = {item.get("idShort") for item in projected}
        additions: list[dict[str, Any]] = []
        for raw_value in raw_elements:
            if not isinstance(raw_value, Mapping):
                continue
            id_short = raw_value.get("idShort")
            if id_short in present:
                continue
            if template.release.key == "digital_nameplate" and id_short == "URIOfTheProduct":
                instance = _metadata(cast(Mapping[str, Any], raw_value))
                instance["value"] = asset_id
                additions.append(instance)
            elif template.release.key == "digital_nameplate" and id_short == "AddressInformation":
                # The Nameplate JSON declares this required drop-in but does not embed
                # its children. Keep the required structural node and report limited
                # deep validation as a warning.
                instance = _metadata(cast(Mapping[str, Any], raw_value))
                additions.append(instance)
        result = [*projected, *additions]
        order = {
            item.get("idShort"): index
            for index, item in enumerate(raw_elements)
            if isinstance(item, Mapping)
        }
        return sorted(result, key=lambda item: order.get(item.get("idShort"), len(order)))

"""Offline ECLASS XML dictionary provider for local development and evaluation."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from mia_dpp.semantic.eclass import EclassProperty, EclassSearchCandidate

_XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
_TOKEN = re.compile(r"[a-z0-9]+")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(element: ElementTree.Element | None) -> str | None:
    if element is None:
        return None
    value = " ".join(part.strip() for part in element.itertext() if part.strip())
    return value or None


def _tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return set(_TOKEN.findall(normalized.casefold()))


def _property_from_xml(element: ElementTree.Element, release: str) -> tuple[EclassProperty, str]:
    children = {_local_name(child.tag): child for child in element}
    irdi = element.attrib.get("id", "").strip()
    preferred_name = _text(children.get("preferred_name"))
    if not irdi or "#02-" not in irdi or not preferred_name:
        raise ValueError("ECLASS XML property must have a property IRDI and preferred name")

    definition = _text(children.get("definition"))
    domain = children.get("domain")
    data_type = domain.attrib.get(_XSI_TYPE) if domain is not None else None
    if data_type:
        data_type = data_type.rsplit(":", 1)[-1].removesuffix("_Type")

    unit_irdi: str | None = None
    if domain is not None:
        unit = next((item for item in domain if _local_name(item.tag) == "unit"), None)
        if unit is not None:
            unit_irdi = unit.attrib.get("unit_ref")

    keyword_text = _text(children.get("keywords")) or ""
    property_ = EclassProperty(
        irdi=irdi,
        preferred_name=preferred_name,
        definition=definition,
        data_type=data_type,
        unit_irdi=unit_irdi,
        release=release,
    )
    return property_, " ".join((preferred_name, definition or "", keyword_text))


class EclassXmlZipProvider:
    """Search and exact-verify properties from local ECLASS dictionary ZIPs.

    Search is a deterministic lexical retrieval step. It never creates concepts:
    every result and every successful direct lookup comes from an XML property
    record carrying the requested IRDI.
    """

    def __init__(self, dictionary_zips: tuple[Path, ...], *, language: str = "en") -> None:
        if not dictionary_zips:
            raise ValueError("At least one ECLASS XML dictionary ZIP is required")
        self._language = language.casefold()
        if self._language not in {"en", "de"}:
            raise ValueError("ECLASS XML language must be 'en' or 'de'")

        properties: dict[str, EclassProperty] = {}
        searchable: dict[str, str] = {}
        preferred = sorted(
            (Path(path) for path in dictionary_zips),
            key=lambda path: (self._language not in path.name.casefold(), path.name.casefold()),
        )
        for path in preferred:
            self._load_dictionary(path, properties, searchable)
        if not properties:
            raise ValueError("No ECLASS property records were found in the configured ZIP files")
        self._properties = properties
        self._searchable = searchable

    @property
    def provider_name(self) -> str:
        return "eclass-local-xml-zip"

    def _load_dictionary(
        self,
        path: Path,
        properties: dict[str, EclassProperty],
        searchable: dict[str, str],
    ) -> None:
        if not path.is_file():
            raise ValueError(f"ECLASS XML dictionary ZIP does not exist: {path}")
        try:
            with ZipFile(path) as archive:
                members = tuple(
                    name
                    for name in archive.namelist()
                    if "Dictionary_" in name and name.casefold().endswith(".xml")
                )
                if not members:
                    raise ValueError(f"No ECLASS dictionary XML files found in {path}")
                for member in members:
                    release_match = re.search(
                        r"ECLASS(\d+_\d+)", member, flags=re.IGNORECASE
                    )
                    release = (
                        release_match.group(1).replace("_", ".")
                        if release_match
                        else "unknown"
                    )
                    with archive.open(member) as source:
                        for _, element in ElementTree.iterparse(source, events=("end",)):
                            if _local_name(element.tag) == "property" and "id" in element.attrib:
                                property_, text = _property_from_xml(element, release)
                                existing = properties.get(property_.irdi)
                                if existing is None:
                                    properties[property_.irdi] = property_
                                searchable[property_.irdi] = " ".join(
                                    part for part in (searchable.get(property_.irdi), text) if part
                                )
                                element.clear()
                            elif _local_name(element.tag) == "class":
                                element.clear()
        except BadZipFile as exc:
            raise ValueError(f"Invalid ECLASS XML dictionary ZIP: {path}") from exc

    async def search_properties(
        self,
        query: str,
        *,
        limit: int,
    ) -> tuple[EclassSearchCandidate, ...]:
        if not query.strip():
            return ()
        if limit < 1:
            raise ValueError("ECLASS search limit must be at least one")
        query_tokens = _tokens(query)
        if not query_tokens:
            return ()

        ranked: list[tuple[float, str, EclassProperty]] = []
        query_text = " ".join(query.casefold().split())
        for irdi, property_ in self._properties.items():
            indexed = self._searchable[irdi]
            indexed_tokens = _tokens(indexed)
            overlap = query_tokens & indexed_tokens
            if not overlap:
                continue
            name_text = " ".join(property_.preferred_name.casefold().split())
            coverage = len(overlap) / len(query_tokens)
            precision = len(overlap) / len(indexed_tokens) if indexed_tokens else 0.0
            phrase_bonus = 1.0 if query_text in name_text else 0.0
            score = coverage * 4.0 + precision + phrase_bonus
            ranked.append((score, irdi, property_))
        ranked.sort(key=lambda item: (-item[0], item[2].preferred_name.casefold(), item[1]))
        return tuple(
            EclassSearchCandidate(
                irdi=property_.irdi,
                preferred_name=property_.preferred_name,
                definition=property_.definition,
            )
            for _, _, property_ in ranked[:limit]
        )

    async def get_property(self, irdi: str) -> EclassProperty | None:
        if not irdi.strip():
            raise ValueError("ECLASS IRDI must not be empty")
        return self._properties.get(irdi.strip())

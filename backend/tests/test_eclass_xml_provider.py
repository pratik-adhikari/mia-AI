"""Tests for the local ECLASS XML ZIP provider."""

from __future__ import annotations

import asyncio
from pathlib import Path
from zipfile import ZipFile

import pytest

from mia_dpp.semantic.eclass_xml import EclassXmlZipProvider


def _dictionary_zip(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    xml = """<?xml version="1.0"?>
    <dictionary xmlns:ontoml="urn:test" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <ontoml:property id="0173-1#02-AAA001#001" xsi:type="ontoml:PROPERTY_Type">
        <ontoml:preferred_name>Rated power</ontoml:preferred_name>
        <ontoml:definition>Maximum rated power of the device.</ontoml:definition>
        <ontoml:domain xsi:type="ontoml:REAL_MEASURE_TYPE_Type">
          <ontoml:unit unit_ref="0173-1#05-AAA153#005" />
        </ontoml:domain>
      </ontoml:property>
      <ontoml:property id="0173-1#02-AAA002#001">
        <ontoml:preferred_name>Nominal voltage</ontoml:preferred_name>
        <ontoml:definition>Nominal electrical voltage.</ontoml:definition>
        <ontoml:domain xsi:type="ontoml:REAL_MEASURE_TYPE_Type" />
      </ontoml:property>
      <ontoml:property property_ref="0173-1#02-AAA001#001" />
    </dictionary>"""
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "ECLASS16_0_Dictionary_ASSET_XML_EN/segment.xml",
            xml,
        )
    return path


def test_local_xml_search_returns_real_dictionary_candidates_and_exact_lookup(
    tmp_path: Path,
) -> None:
    provider = EclassXmlZipProvider((_dictionary_zip(tmp_path / "ECLASS16_0_enUSassetXML.zip"),))

    async def run():
        candidates = await provider.search_properties("rated power", limit=5)
        verified = await provider.get_property("0173-1#02-AAA001#001")
        missing = await provider.get_property("0173-1#02-DOES_NOT_EXIST#001")
        return candidates, verified, missing

    candidates, verified, missing = asyncio.run(run())

    assert provider.provider_name == "eclass-local-xml-zip"
    assert candidates[0].irdi == "0173-1#02-AAA001#001"
    assert verified is not None
    assert verified.irdi == candidates[0].irdi
    assert verified.preferred_name == "Rated power"
    assert verified.data_type == "REAL_MEASURE_TYPE"
    assert verified.unit_irdi == "0173-1#05-AAA153#005"
    assert verified.release == "16.0"
    assert missing is None


def test_local_xml_provider_rejects_archives_without_dictionary_records(
    tmp_path: Path,
) -> None:
    path = tmp_path / "empty.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("readme.txt", "not a dictionary")

    with pytest.raises(ValueError, match="No ECLASS dictionary XML files"):
        EclassXmlZipProvider((path,))


def test_local_xml_provider_requires_exact_language_option(tmp_path: Path) -> None:
    path = _dictionary_zip(tmp_path / "ECLASS16_0_enUSassetXML.zip")
    with pytest.raises(ValueError, match="language must be"):
        EclassXmlZipProvider((path,), language="fr")

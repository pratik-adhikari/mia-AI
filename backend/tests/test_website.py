"""Behavioral website tests over Crawl4AI structured extraction."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from mia_dpp.aas.build import build_dpp
from mia_dpp.aas.requirements import build_template_index
from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.agent.models import ProductWork
from mia_dpp.domain.mappings import FieldMapping, MappingStatus
from mia_dpp.errors import ExtractionError, MappingError
from mia_dpp.integrations.crawl4ai import Crawl4AIPageLoader
from mia_dpp.tools.mapping.mapper import DeterministicWebsiteMapper
from mia_dpp.tools.web.models import (
    ExtractionSchema,
    ProductUrlRejectedError,
    RenderedPage,
    SourceImage,
    SourceLink,
)
from mia_dpp.tools.web.schema import WebSchemaStore
from mia_dpp.tools.web.tool import WebExtractionTool
from mia_dpp.tools.web.url_policy import ProductUrlPolicy

FIXTURE = Path(__file__).parent / "fixtures" / "web" / "website-product.html"
PRODUCT_URL = "https://manufacturer.example/products/pg-16"
AFRISO_URL = (
    "https://www.afriso.com/products/domestic-technology/level-indicators-and-"
    "level-controllers/tankcontrol-20-25/52161-fuellstandmessgeraet-tankcontrol-25"
)
ACQUIRED_AT = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
FIXTURE_SCHEMA: ExtractionSchema = {
    "name": "generic product fixture",
    "baseSelector": "html",
    "fields": [
        {
            "name": "product",
            "selector": "h1",
            "type": "nested",
            "fields": [{"name": "name", "type": "text"}],
        },
        {
            "name": "jsonLd",
            "selector": "script[type='application/ld+json']",
            "type": "text",
        },
        {
            "name": "tableFacts",
            "selector": "table tr",
            "type": "nested_list",
            "fields": [
                {"name": "label", "selector": "th, td:first-child", "type": "text"},
                {"name": "value", "selector": "td:nth-child(2)", "type": "text"},
            ],
        },
        {
            "name": "definitionFacts",
            "selector": "dl",
            "type": "nested_list",
            "fields": [
                {"name": "label", "selector": "dt", "type": "text"},
                {"name": "value", "selector": "dd", "type": "text"},
            ],
        },
    ],
}


async def public_resolver(host: str, port: int) -> tuple[str, ...]:
    assert host
    assert port in {80, 443}
    return ("93.184.216.34",)


def product_page(url: str = PRODUCT_URL, html: str | None = None) -> RenderedPage:
    page = RenderedPage(url=url, html=html or FIXTURE.read_text(), acquired_at=ACQUIRED_AT)
    data = Crawl4AIPageLoader().extract(page, FIXTURE_SCHEMA)
    return replace(
        page,
        structured_data=data,
        schema_id="fixture-schema",
        base_selector="html",
    )


class FixtureLoader:
    def __init__(self, page: RenderedPage | None = None) -> None:
        self.page = page or product_page()
        self.calls: list[str] = []
        self.generated = 0

    async def load(self, url: str, schema: ExtractionSchema | None = None) -> RenderedPage:
        self.calls.append(url)
        if schema is None:
            return self.page
        return replace(self.page, structured_data=Crawl4AIPageLoader().extract(self.page, schema))

    def extract(
        self, page: RenderedPage, schema: ExtractionSchema
    ) -> tuple[dict[str, object], ...]:
        return Crawl4AIPageLoader().extract(page, schema)

    async def generate_schema(self, page: RenderedPage) -> ExtractionSchema:
        self.generated += 1
        return FIXTURE_SCHEMA

    async def discover(self, url: str) -> tuple[SourceLink, ...]:
        return ()


async def ingest(
    loader: FixtureLoader | None = None,
    *,
    template_keys: tuple[str, ...] = ("digital_nameplate", "technical_data"),
) -> ProductWork:
    repository = OfficialTemplateRepository()
    package, _ = await WebExtractionTool(
        loader=loader or FixtureLoader(),
        url_policy=ProductUrlPolicy(public_resolver),
    ).extract(PRODUCT_URL)
    mapping = await DeterministicWebsiteMapper(repository).propose(package.evidence)
    return ProductWork(
        product_id=package.product_id,
        product_name=package.product_name,
        source_urls=(package.evidence[0].source_uri,),
        source_artifact_ids=package.source_artifact_ids,
        evidence=package.evidence,
        mapping_result=mapping,
        template_index=build_template_index(tuple(repository.load(key) for key in template_keys)),
    )


def resolved_mappings(work: ProductWork) -> tuple[FieldMapping, ...]:
    assert work.mapping_result is not None
    return (
        *work.mapping_result.mapped,
        *work.mapping_result.ambiguous,
        *work.mapping_result.rejected,
    )


def test_crawl4ai_schema_extracts_tables_definitions_and_json_ld() -> None:
    page = product_page()
    record = page.structured_data[0]

    assert record["product"] == {"name": "Pressure Gauge PG-16"}
    assert {item["label"] for item in record["tableFacts"]} >= {
        "Measuring range",
        "Operating temperature",
    }
    assert json.loads(record["jsonLd"])["sku"] == "63820"


def test_structured_json_becomes_provenance_rich_evidence() -> None:
    package, page = asyncio.run(
        WebExtractionTool(
            loader=FixtureLoader(),
            url_policy=ProductUrlPolicy(public_resolver),
        ).extract(PRODUCT_URL)
    )
    by_label = {item.source_label: item for item in package.evidence}

    assert package.product_name == "Pressure Gauge PG-16"
    assert {"Manufacturer", "SKU", "Measuring range", "Protocol", "Material"} <= by_label.keys()
    assert by_label["Supply voltage"].unit == "V"
    assert by_label["Measuring range"].source_location.base_selector == "html"
    assert by_label["Measuring range"].source_location.schema_id == "fixture-schema"
    assert by_label["Measuring range"].source_location.record_path == (0, 0)
    assert {item.source_content_sha256 for item in package.evidence} == {page.content_sha256}
    assert {item.acquired_at for item in package.evidence} == {ACQUIRED_AT}


def test_grouped_schema_preserves_repeated_label_context() -> None:
    html = """
    <body><h1>Controller</h1><div class="group"><h2>Submersible probe</h2>
    <dl><dt>Degree of protection</dt><dd>IP68</dd></dl></div>
    <div class="group"><h2>Housing</h2>
    <dl><dt>Degree of protection</dt><dd>IP54</dd></dl></div></body>
    """
    schema: ExtractionSchema = {
        "name": "grouped specifications",
        "baseSelector": "body",
        "fields": [
            {
                "name": "product",
                "selector": "h1",
                "type": "nested",
                "fields": [{"name": "name", "type": "text"}],
            },
            {
                "name": "sections",
                "selector": ".group",
                "type": "nested_list",
                "fields": [
                    {"name": "name", "selector": "h2", "type": "text"},
                    {
                        "name": "properties",
                        "selector": "dl",
                        "type": "nested_list",
                        "fields": [
                            {"name": "label", "selector": "dt", "type": "text"},
                            {"name": "value", "selector": "dd", "type": "text"},
                        ],
                    },
                ],
            },
        ],
    }
    page = RenderedPage(url=PRODUCT_URL, html=html, acquired_at=ACQUIRED_AT)
    structured = Crawl4AIPageLoader().extract(page, schema)
    loader = FixtureLoader(replace(page, structured_data=structured, schema_id="grouped"))
    package, _ = asyncio.run(
        WebExtractionTool(loader=loader, url_policy=ProductUrlPolicy(public_resolver)).extract(
            PRODUCT_URL
        )
    )
    protection = [item for item in package.evidence if item.source_label == "Degree of protection"]

    assert [(item.context_path, item.value) for item in protection] == [
        (("Submersible probe",), "IP68"),
        (("Housing",), "IP54"),
    ]
    assert protection[0].id != protection[1].id


def test_bundled_afriso_schema_preserves_known_group_hierarchy() -> None:
    html = """
    <html><body><h1>TankControl 25</h1><div class="tech-table">
      <div class="group"><dt class="groupingProperty">Operating temperature range</dt>
        <dl class="d-flex"><dt>Medium</dt><dd>-5/+70 °C</dd></dl>
        <dl class="d-flex"><dt>Ambient</dt><dd>-5/+55 °C</dd></dl>
        <dl class="d-flex"><dt>Storage</dt><dd>-5/+65 °C</dd></dl></div>
      <div class="group"><dt class="groupingProperty">Submersible probe</dt>
        <dl class="d-flex"><dt>Degree of protection</dt><dd>IP 68</dd></dl></div>
      <div class="group"><dt class="groupingProperty">Housing</dt>
        <dl class="d-flex"><dt>Degree of protection</dt><dd>IP 54</dd></dl></div>
    </div></body></html>
    """
    schema = WebSchemaStore().load(AFRISO_URL)
    assert schema is not None
    page = RenderedPage(url=AFRISO_URL, html=html, acquired_at=ACQUIRED_AT)
    structured = Crawl4AIPageLoader().extract(page, schema.extraction_schema)
    source = replace(page, structured_data=structured, schema_id=schema.id)
    package, _ = asyncio.run(
        WebExtractionTool(
            loader=FixtureLoader(source),
            url_policy=ProductUrlPolicy(public_resolver),
        ).extract(AFRISO_URL)
    )

    relationships = {
        (item.context_path, item.source_label, item.value) for item in package.evidence
    }
    assert (("Operating temperature range",), "Medium", "-5/+70 °C") in relationships
    assert (("Operating temperature range",), "Ambient", "-5/+55 °C") in relationships
    assert (("Operating temperature range",), "Storage", "-5/+65 °C") in relationships
    assert (("Submersible probe",), "Degree of protection", "IP 68") in relationships
    assert (("Housing",), "Degree of protection", "IP 54") in relationships


def test_crawl4ai_links_and_media_become_relevant_assets() -> None:
    page = replace(
        product_page(),
        links=(
            SourceLink("https://manufacturer.example/tankcontrol.pdf", "Datasheet"),
            SourceLink("https://manufacturer.example/download/42", "CAD drawing"),
            SourceLink("https://manufacturer.example/legal", "Legal"),
        ),
        images=(
            SourceImage("https://manufacturer.example/product.jpg", "Pressure Gauge", score=5),
            SourceImage("https://manufacturer.example/logo.svg", "Company logo", score=5),
            SourceImage("https://manufacturer.example/pixel.gif", score=1, width=1),
        ),
    )
    _, source = asyncio.run(
        WebExtractionTool(
            loader=FixtureLoader(page),
            url_policy=ProductUrlPolicy(public_resolver),
        ).extract(PRODUCT_URL)
    )

    assert [(item.kind, item.url) for item in source.assets] == [
        ("document", "https://manufacturer.example/tankcontrol.pdf"),
        ("document", "https://manufacturer.example/download/42"),
        ("image", "https://manufacturer.example/product.jpg"),
    ]


def test_selected_asset_download_records_hash_and_media_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"%PDF-1.7 fixture"
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            content=data,
            headers={"content-type": "application/pdf"},
        )
    )
    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=transport, **kwargs),
    )
    tool = WebExtractionTool(
        loader=FixtureLoader(),
        url_policy=ProductUrlPolicy(public_resolver),
    )

    asset, downloaded = asyncio.run(
        tool.download_source_asset("https://manufacturer.example/tankcontrol.pdf")
    )

    assert downloaded == data
    assert asset.media_type == "application/pdf"
    assert asset.content_sha256 == hashlib.sha256(data).hexdigest()


def test_generated_schema_is_validated_once_then_reused(tmp_path: Path) -> None:
    raw_page = replace(product_page(), structured_data=(), schema_id=None, base_selector=None)
    loader = FixtureLoader(raw_page)
    schemas = WebSchemaStore(tmp_path)
    tool = WebExtractionTool(
        loader=loader,
        schemas=schemas,
        url_policy=ProductUrlPolicy(public_resolver),
    )

    first, _ = asyncio.run(tool.extract(PRODUCT_URL))
    second, _ = asyncio.run(tool.extract(PRODUCT_URL))

    assert first.evidence == second.evidence
    assert loader.generated == 1
    assert schemas.load(PRODUCT_URL) is not None


def test_empty_generated_schema_is_not_trusted(tmp_path: Path) -> None:
    class EmptyLoader(FixtureLoader):
        def extract(
            self, page: RenderedPage, schema: ExtractionSchema
        ) -> tuple[dict[str, object], ...]:
            return ({},)

    schemas = WebSchemaStore(tmp_path)
    tool = WebExtractionTool(
        loader=EmptyLoader(replace(product_page(), structured_data=())),
        schemas=schemas,
        url_policy=ProductUrlPolicy(public_resolver),
    )

    with pytest.raises(ExtractionError, match="no meaningful product facts"):
        asyncio.run(tool.extract(PRODUCT_URL))
    assert schemas.load(PRODUCT_URL) is None


def test_website_maps_without_discarding_unmatched_evidence() -> None:
    work = asyncio.run(ingest())
    assert work.mapping_result is not None
    targets = {item.target.id_short for item in resolved_mappings(work)}

    assert {
        "ManufacturerName",
        "ManufacturerProductDesignation",
        "SerialNumber",
        "OrderCodeOfManufacturer",
        "YearOfConstruction",
        "CountryOfOrigin",
        "DegreeOfProtection",
        "MeasuringRange",
    } <= targets
    unknown = {
        item.id
        for item in work.evidence
        if item.source_label in {"Processor", "Protocol", "Material"}
    }
    assert unknown <= set(work.mapping_result.unmatched_evidence_ids)
    assert work.coverage_report.statistics.requirements == 79


def test_website_coverage_accepts_template_selection() -> None:
    work = asyncio.run(ingest(template_keys=("technical_data",)))
    assert work.coverage_report.statistics.selected_templates == 1
    assert work.coverage_report.statistics.requirements == 48


def test_website_evidence_survives_aas_compilation() -> None:
    work = asyncio.run(ingest())
    accepted = [
        mapping.model_copy(update={"id": f"mapping-{index}", "status": MappingStatus.APPROVED})
        for index, mapping in enumerate(resolved_mappings(work))
        if mapping.target.id_short != "MarkingName"
    ]
    package = build_dpp(work.product_name, accepted, evidence=work.evidence, now=ACQUIRED_AT)

    assert package.deployable
    assert package.validation_report.valid
    assert {item.source_uri for item in package.evidence} == {PRODUCT_URL}


def test_compiler_rejects_mapping_without_its_evidence() -> None:
    work = asyncio.run(ingest())
    mapping = resolved_mappings(work)[0]
    accepted = mapping.model_copy(update={"id": "mapping-missing-evidence", "status": "approved"})
    with pytest.raises(MappingError, match="missing supplied evidence"):
        build_dpp(
            work.product_name,
            [accepted],
            evidence=tuple(item for item in work.evidence if item.id != mapping.evidence_id),
            now=ACQUIRED_AT,
        )


@pytest.mark.parametrize(
    "url,addresses,message",
    [
        ("file:///etc/passwd", ("93.184.216.34",), "absolute HTTP"),
        ("https://user:secret@example.com/product", ("93.184.216.34",), "credentials"),
        ("http://127.0.0.1/admin", ("127.0.0.1",), "private"),
        ("http://example.com:8080/product", ("93.184.216.34",), "port 80 or 443"),
    ],
)
def test_url_policy_rejects_unsafe_targets(
    url: str, addresses: tuple[str, ...], message: str
) -> None:
    async def resolver(host: str, port: int) -> tuple[str, ...]:
        return addresses

    with pytest.raises(ProductUrlRejectedError, match=message):
        asyncio.run(ProductUrlPolicy(resolver).validate(url))


def test_web_tool_checks_final_crawl4ai_url() -> None:
    async def resolver(host: str, port: int) -> tuple[str, ...]:
        return ("127.0.0.1",) if host == "localhost" else ("93.184.216.34",)

    loader = FixtureLoader(replace(product_page(), url="http://localhost/internal"))
    with pytest.raises(ProductUrlRejectedError, match="private"):
        asyncio.run(
            WebExtractionTool(loader=loader, url_policy=ProductUrlPolicy(resolver)).extract(
                PRODUCT_URL
            )
        )


def test_raw_source_hash_remains_auditable() -> None:
    package, page = asyncio.run(
        WebExtractionTool(
            loader=FixtureLoader(), url_policy=ProductUrlPolicy(public_resolver)
        ).extract(PRODUCT_URL)
    )
    expected = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert page.content_sha256 == expected
    assert {item.source_content_sha256 for item in package.evidence} == {expected}

"""Authoritative ECLASS JSON provider boundary tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from mia_dpp.semantic.eclass import EclassJsonV2Provider


def test_eclass_provider_searches_then_fetches_property_by_irdi() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(str(request.url))
        if request.url.path.endswith("/jsonapi/v2/properties"):
            assert request.url.params["preferredName"] == "Repeat accuracy"
            return httpx.Response(
                200,
                json={
                    "content": [
                        {
                            "irdi": "0173-1#02-AAO677#001",
                            "preferredName": {"en": "Position repeatability"},
                            "definition": {"en": "Repeatability of positioning."},
                        }
                    ]
                },
            )
        assert request.url.path.endswith(
            "/jsonapi/v2/properties/0173-1-02-AAO677-001"
        )
        return httpx.Response(
            200,
            json={
                "irdi": "0173-1#02-AAO677#001",
                "preferredName": [
                    {"language": "en", "label": "Position repeatability"}
                ],
                "definition": [
                    {"language": "en", "label": "Repeatability of positioning."}
                ],
                "dataType": "REAL_MEASURE",
                "unit": {
                    "irdi": "0173-1#05-AAA480#002",
                    "symbol": "mm",
                },
                "release": "15.0",
            },
        )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://registry.example/jsonapi/v2",
        ) as client:
            provider = EclassJsonV2Provider(
                certificate_file=Path("/unused-in-injected-client.pem"),
                base_url="https://registry.example/jsonapi/v2",
                client=client,
            )
            hits = await provider.search_properties("Repeat accuracy", limit=10)
            property_ = await provider.get_property(hits[0].irdi)
            return hits, property_

    hits, property_ = asyncio.run(run())

    assert len(hits) == 1
    assert hits[0].irdi == "0173-1#02-AAO677#001"
    assert property_ is not None
    assert property_.irdi == hits[0].irdi
    assert property_.preferred_name == "Position repeatability"
    assert property_.unit_symbol == "mm"
    assert property_.release == "15.0"
    assert len(requested_paths) == 2


def test_eclass_direct_lookup_rejects_wrong_irdi() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "irdi": "wrong-irdi",
                "preferredName": {"en": "Wrong property"},
            },
        )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://registry.example/jsonapi/v2",
        ) as client:
            provider = EclassJsonV2Provider(
                certificate_file=Path("/unused.pem"),
                base_url="https://registry.example/jsonapi/v2",
                client=client,
            )
            return await provider.get_property("0173-1#02-AAO677#001")

    with pytest.raises(ValueError, match="expected"):
        asyncio.run(run())

"""Authoritative ECLASS property-provider boundary.

The semantic engine consumes typed ECLASS properties. Registry transport and
authentication stay behind this boundary so Jev can never invent an IRDI.
"""

from __future__ import annotations

import ssl
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import httpx
from pydantic import Field

from mia_dpp.domain.base import WireModel


class EclassProperty(WireModel):
    """One authoritative ECLASS property resolved by IRDI."""

    irdi: str = Field(min_length=1)
    preferred_name: str = Field(min_length=1)
    definition: str | None = None
    data_type: str | None = None
    unit_irdi: str | None = None
    unit_symbol: str | None = None
    release: str | None = None


class EclassSearchCandidate(WireModel):
    """A search hit before direct-IRDI verification."""

    irdi: str = Field(min_length=1)
    preferred_name: str = Field(min_length=1)
    definition: str | None = None


class EclassPropertyProvider(Protocol):
    """Minimal authoritative registry contract used by semantic resolution."""

    @property
    def provider_name(self) -> str: ...

    async def search_properties(
        self,
        query: str,
        *,
        limit: int,
    ) -> tuple[EclassSearchCandidate, ...]: ...

    async def get_property(self, irdi: str) -> EclassProperty | None: ...


def _localized_text(value: object, *, language: str = "en") -> str | None:
    """Read ECLASS v1 language maps or v2 localized arrays without guessing."""

    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, Mapping):
        preferred = (
            value.get(f"{language}-US")
            or value.get(language)
            or value.get("en-US")
            or value.get("en")
        )
        if isinstance(preferred, str) and preferred.strip():
            return preferred.strip()
        for candidate in value.values():
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        fallback: str | None = None
        for item in value:
            if not isinstance(item, Mapping):
                continue
            label = item.get("label") or item.get("text") or item.get("value")
            if not isinstance(label, str) or not label.strip():
                continue
            language_code = str(item.get("language") or "").casefold()
            if language_code in {language.casefold(), f"{language.casefold()}-us"}:
                return label.strip()
            if language_code in {"en", "en-us"}:
                fallback = fallback or label.strip()
            elif fallback is None:
                fallback = label.strip()
        return fallback
    return None


def _first_text(payload: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _irdi(payload: Mapping[str, object]) -> str | None:
    return _first_text(payload, "irdi", "IRDI", "IrdiPR", "id")


def _preferred_name(payload: Mapping[str, object]) -> str | None:
    for key in ("preferredName", "preferred_name", "name"):
        text = _localized_text(payload.get(key))
        if text:
            return text
    return None


def _definition(payload: Mapping[str, object]) -> str | None:
    for key in ("definition", "Definition"):
        text = _localized_text(payload.get(key))
        if text:
            return text
    return None


def _list_payload(payload: object) -> list[Mapping[str, object]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    for key in ("content", "items", "properties", "results", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
    if _irdi(payload):
        return [payload]
    return []


def _unit_fields(payload: Mapping[str, object]) -> tuple[str | None, str | None]:
    unit = payload.get("unit")
    if isinstance(unit, Mapping):
        unit_irdi = _irdi(unit)
        symbol = _first_text(unit, "symbol", "shortName", "code")
        return unit_irdi, symbol
    return (
        _first_text(payload, "unitIrdi", "unitIRDI"),
        _first_text(payload, "unitSymbol"),
    )


def _property_from_payload(payload: Mapping[str, object]) -> EclassProperty:
    irdi = _irdi(payload)
    preferred_name = _preferred_name(payload)
    if not irdi or not preferred_name:
        raise ValueError("ECLASS property payload must contain IRDI and preferred name")
    unit_irdi, unit_symbol = _unit_fields(payload)
    return EclassProperty(
        irdi=irdi,
        preferred_name=preferred_name,
        definition=_definition(payload),
        data_type=_first_text(
            payload,
            "dataType",
            "datatype",
            "propertyDataType",
            "dataTypeDefinition",
        ),
        unit_irdi=unit_irdi,
        unit_symbol=unit_symbol,
        release=_first_text(payload, "release", "releaseVersion", "version"),
    )


def _v2_path_irdi(irdi: str) -> str:
    """Convert canonical IRDI separators to the JSON V2 endpoint path form."""

    return quote(irdi.replace("#", "-"), safe="-_.")


class EclassJsonV2Provider:
    """mTLS-capable adapter for the official ECLASS JSON webservice.

    ECLASS documents JSON list retrieval with preferred-name search and direct
    property lookup by IRDI. Search parameter names remain configurable so the
    transport assumption is isolated from semantic code.
    """

    def __init__(
        self,
        *,
        certificate_file: Path,
        key_file: Path | None = None,
        base_url: str = "https://www.eclass-cdp.com/jsonapi/v2",
        search_parameter: str = "preferredName",
        accept_language: str = "en-US",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._search_parameter = search_parameter
        self._accept_language = accept_language
        self._client = client
        self._ssl_context: ssl.SSLContext | None = None
        if client is None:
            certificate_file = Path(certificate_file)
            key_path = Path(key_file) if key_file is not None else None
            if not certificate_file.is_file():
                raise ValueError(f"ECLASS certificate file does not exist: {certificate_file}")
            if key_path is not None and not key_path.is_file():
                raise ValueError(f"ECLASS key file does not exist: {key_path}")
            ssl_context = ssl.create_default_context()
            ssl_context.load_cert_chain(
                certfile=str(certificate_file),
                keyfile=str(key_path) if key_path is not None else None,
            )
            self._ssl_context = ssl_context

    @property
    def provider_name(self) -> str:
        return "eclass-json-v2"

    async def _request(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        headers = {
            "Accept": "application/json",
            "Accept-Language": self._accept_language,
        }
        url = f"{self._base_url}/{path.lstrip('/')}"
        if self._client is not None:
            response = await self._client.get(url, params=params, headers=headers)
        else:
            assert self._ssl_context is not None
            async with httpx.AsyncClient(
                verify=self._ssl_context,
                timeout=30.0,
            ) as client:
                response = await client.get(url, params=params, headers=headers)
        return response

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
        response = await self._request(
            "/properties",
            params={self._search_parameter: query.strip()},
        )
        response.raise_for_status()
        candidates: list[EclassSearchCandidate] = []
        seen: set[str] = set()
        for item in _list_payload(response.json()):
            irdi = _irdi(item)
            preferred_name = _preferred_name(item)
            if not irdi or not preferred_name or irdi in seen:
                continue
            seen.add(irdi)
            candidates.append(
                EclassSearchCandidate(
                    irdi=irdi,
                    preferred_name=preferred_name,
                    definition=_definition(item),
                )
            )
            if len(candidates) >= limit:
                break
        return tuple(candidates)

    async def get_property(self, irdi: str) -> EclassProperty | None:
        if not irdi.strip():
            raise ValueError("ECLASS IRDI must not be empty")
        encoded = _v2_path_irdi(irdi.strip())
        response = await self._request(f"/properties/{encoded}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        items = _list_payload(response.json())
        if len(items) != 1:
            raise ValueError(
                f"ECLASS direct property lookup returned {len(items)} records for {irdi!r}"
            )
        property_ = _property_from_payload(items[0])
        if property_.irdi != irdi:
            raise ValueError(
                f"ECLASS direct lookup returned IRDI {property_.irdi!r}, expected {irdi!r}"
            )
        return property_

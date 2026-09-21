"""HTTP request and response contracts exposed by MIA."""

from __future__ import annotations

from typing import Literal

from mia_dpp.domain.base import WireModel
from mia_dpp.domain.evidence import EvidenceRecord
from mia_dpp.domain.mappings import FieldMapping
from mia_dpp.domain.product import DppVersion, ProductRecord, ProductRun
from mia_dpp.storage.models import StoredArtifact


class DppBuildRequest(WireModel):
    product_name: str
    mappings: tuple[FieldMapping, ...]
    evidence: tuple[EvidenceRecord, ...] = ()


class HealthResponse(WireModel):
    status: Literal["ok", "not_ready"]
    version: str
    standards_ready: bool
    standards_commit: str


class ProductLibraryItem(WireModel):
    product: ProductRecord
    latest_dpp: DppVersion | None = None
    run_count: int = 0


class ProductDetail(WireModel):
    product: ProductRecord
    runs: tuple[ProductRun, ...] = ()
    dpp_versions: tuple[DppVersion, ...] = ()
    artifacts: tuple[StoredArtifact, ...] = ()

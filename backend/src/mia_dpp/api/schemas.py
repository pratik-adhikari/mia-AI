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
    thread_id: str | None = None
    product_id: str | None = None


class HealthResponse(WireModel):
    status: Literal["ok", "not_ready"]
    version: str
    standards_ready: bool
    standards_commit: str


class ProductLibraryItem(WireModel):
    product: ProductRecord
    latest_dpp: DppVersion | None = None
    latest_run: ProductRun | None = None
    run_count: int = 0
    resumable: bool = False
    resume_thread_id: str | None = None
    workflow_status: str = "idle"
    human_reviewed_mappings: int = 0
    human_dummy_mappings: int = 0


class ProductDetail(WireModel):
    product: ProductRecord
    runs: tuple[ProductRun, ...] = ()
    dpp_versions: tuple[DppVersion, ...] = ()
    artifacts: tuple[StoredArtifact, ...] = ()


class StorageStatus(WireModel):
    """Safe runtime persistence diagnostics without exposing credentials."""

    database_backend: Literal["sqlite", "postgres"]
    database_provider: Literal["local", "postgres", "supabase"]
    artifact_backend: Literal["filesystem", "vercel_blob"]
    durable_metadata: bool
    durable_artifacts: bool

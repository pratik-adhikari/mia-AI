"""Composition helpers for durable local and Vercel runtime dependencies."""

from __future__ import annotations

from mia_dpp.config import Settings
from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.storage.base import ArtifactStore
from mia_dpp.storage.local import LocalArtifactStore
from mia_dpp.storage.vercel_blob import VercelBlobArtifactStore


def create_catalogue(settings: Settings) -> ProductCatalogue:
    location = settings.database_url or settings.catalogue_path
    return ProductCatalogue(location)


def create_artifact_store(settings: Settings) -> ArtifactStore:
    if not settings.vercel_environment:
        return LocalArtifactStore(settings.workspace_root)
    if not settings.database_url:
        raise RuntimeError("Vercel deployment requires MIA_DATABASE_URL for durable metadata")
    if not (settings.blob_read_write_token or settings.blob_store_id):
        raise RuntimeError(
            "Vercel deployment requires a connected Blob store "
            "(BLOB_READ_WRITE_TOKEN or BLOB_STORE_ID)"
        )
    token = (
        settings.blob_read_write_token.get_secret_value()
        if settings.blob_read_write_token is not None
        else None
    )
    return VercelBlobArtifactStore(token=token)

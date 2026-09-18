"""Vercel Blob adapter for durable production artifacts."""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from mia_dpp.storage.models import StoredArtifact


class VercelBlobArtifactStore:
    """Store private workflow artifacts through Vercel's official Python SDK."""

    def __init__(self, *, token: str | None = None, client: Any | None = None) -> None:
        if client is None:
            try:
                from vercel.blob import BlobClient  # type: ignore[import-not-found]
            except ImportError as error:  # pragma: no cover - production dependency
                raise RuntimeError(
                    "Vercel Blob requires the production dependency group"
                ) from error
            client = BlobClient(token=token) if token else BlobClient()
        self._client = client

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        product_id: str | None = None,
        run_id: str | None = None,
        derived_from: tuple[str, ...] = (),
    ) -> StoredArtifact:
        artifact_id = f"artifact-{uuid.uuid4().hex}"
        path = "/".join(
            (
                "mia",
                self._safe(product_id or "shared"),
                self._safe(run_id or "shared"),
                artifact_id,
                self._safe(key),
            )
        )
        uploaded = self._client.put(
            path,
            data,
            access="private",
            content_type=content_type,
            add_random_suffix=False,
        )
        return StoredArtifact(
            id=artifact_id,
            key=key,
            content_type=content_type,
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
            storage_uri=uploaded.url,
            product_id=product_id,
            run_id=run_id,
            derived_from=derived_from,
        )

    def get(self, artifact: StoredArtifact) -> bytes:
        value = self._client.get(artifact.storage_uri)
        if isinstance(value, bytes):
            return value
        if hasattr(value, "read"):
            return bytes(value.read())
        if hasattr(value, "content"):
            return bytes(value.content)
        return bytes(value)

    @staticmethod
    def _safe(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.") or "artifact"

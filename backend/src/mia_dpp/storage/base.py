"""Artifact-byte storage contract."""

from typing import Protocol

from mia_dpp.storage.models import StoredArtifact


class ArtifactStore(Protocol):
    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        product_id: str | None = None,
        run_id: str | None = None,
        derived_from: tuple[str, ...] = (),
    ) -> StoredArtifact: ...

    def get(self, artifact: StoredArtifact) -> bytes: ...

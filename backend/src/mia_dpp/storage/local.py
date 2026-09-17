"""Filesystem artifact storage for local development and deterministic tests."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from mia_dpp.storage.models import StoredArtifact


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

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
        relative = Path(run_id or "shared") / artifact_id / self._safe_key(key)
        path = (self._root / relative).resolve()
        if self._root not in path.parents:
            raise ValueError("artifact path escapes storage root")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return StoredArtifact(
            id=artifact_id,
            key=key,
            content_type=content_type,
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
            storage_uri=relative.as_posix(),
            product_id=product_id,
            run_id=run_id,
            derived_from=derived_from,
        )

    def get(self, artifact: StoredArtifact) -> bytes:
        path = (self._root / artifact.storage_uri).resolve()
        if self._root not in path.parents:
            raise ValueError("artifact path escapes storage root")
        return path.read_bytes()

    @staticmethod
    def _safe_key(key: str) -> str:
        return "-".join(part for part in Path(key).parts if part not in {"", ".", ".."})

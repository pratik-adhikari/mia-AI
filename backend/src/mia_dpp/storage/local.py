"""Filesystem artifact storage for local development and deterministic tests."""

from __future__ import annotations

import hashlib
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
        digest = hashlib.sha256(data).hexdigest()
        identity = hashlib.sha256(f"{run_id or 'shared'}\0{key}\0{digest}".encode()).hexdigest()[
            :24
        ]
        artifact_id = f"artifact-{identity}"
        # Preserve logical keys (images/, documents/, evidence/) so humans can browse a run.
        relative = Path(run_id or "shared") / self._safe_key(key)
        path = (self._root / relative).resolve()
        if self._root not in path.parents:
            raise ValueError("artifact path escapes storage root")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != data:
            # Artifacts are immutable; retain both versions instead of overwriting an earlier file.
            path = path.with_name(f"{path.stem}-{artifact_id}{path.suffix}")
            relative = path.relative_to(self._root)
        path.write_bytes(data)
        return StoredArtifact(
            id=artifact_id,
            key=key,
            content_type=content_type,
            sha256=digest,
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
    def _safe_key(key: str) -> Path:
        """Keep useful subdirectories while removing path-traversal components."""

        return Path(*(part for part in Path(key).parts if part not in {"", ".", "..", "/"}))

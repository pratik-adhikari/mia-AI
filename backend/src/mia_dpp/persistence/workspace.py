"""Thread-oriented compatibility view over durable catalogue + artifact storage."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import PurePosixPath

from mia_dpp.agent.models import AgentTraceEvent, TraceStatus
from mia_dpp.persistence.catalogue import LOCAL_USER_ID, ProductCatalogue
from mia_dpp.storage.base import ArtifactStore
from mia_dpp.storage.models import ArtifactKind, StoredArtifact, WorkspaceArtifact
from mia_dpp.tools.mapping.models import MappingKnowledgeEntry


class WorkspaceView:
    """Preserve the existing workspace API while storage becomes deployment-safe."""

    def __init__(self, catalogue: ProductCatalogue, artifacts: ArtifactStore) -> None:
        self._catalogue = catalogue
        self._artifacts = artifacts

    def list_artifacts(
        self,
        thread_id: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> tuple[WorkspaceArtifact, ...]:
        return tuple(self._view(item) for item in self._stored(thread_id, user_id=user_id))

    def read_artifact(
        self,
        thread_id: str,
        artifact_id: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> tuple[WorkspaceArtifact, bytes]:
        allowed = {item.id for item in self._stored(thread_id, user_id=user_id)}
        if artifact_id not in allowed:
            raise KeyError("unknown artifact")
        artifact = self._catalogue.get_artifact(artifact_id)
        if artifact is None:
            raise KeyError("unknown artifact")
        return self._view(artifact), self._artifacts.get(artifact)

    def list_events(
        self,
        thread_id: str,
        offset: int = 0,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> tuple[AgentTraceEvent, ...]:
        events = [
            event
            for run in self._catalogue.list_runs_for_thread(thread_id, user_id=user_id)
            for event in self._catalogue.list_events(run.id, user_id=user_id)
        ][offset:]
        result = []
        for event in events:
            run = self._catalogue.get_run(event.run_id)
            result.append(
                AgentTraceEvent(
                    id=event.id,
                    thread_id=thread_id,
                    event_type=event.event_type,
                    status=TraceStatus.COMPLETED,
                    timestamp=event.timestamp,
                    summary=event.summary,
                    product_id=run.product_id if run else None,
                    metadata={key: self._scalar(value) for key, value in event.metadata.items()},
                )
            )
        return tuple(result)

    def export_zip(self, thread_id: str, *, user_id: str = LOCAL_USER_ID) -> bytes:
        artifacts = self._stored(thread_id, user_id=user_id)
        output = BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(
                    [self._view(item).model_dump(mode="json") for item in artifacts],
                    indent=2,
                ),
            )
            names: set[str] = set()
            for artifact in artifacts:
                name = self._archive_name(artifact, names)
                names.add(name)
                archive.writestr(name, self._artifacts.get(artifact))
        return output.getvalue()

    def combined_export(
        self,
        thread_id: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> dict[str, object]:
        artifacts = self._stored(thread_id, user_id=user_id)
        contents: dict[str, object] = {}
        for artifact in artifacts:
            if "json" in artifact.content_type:
                contents[artifact.id] = json.loads(self._artifacts.get(artifact))
        return {
            "artifacts": [self._view(item).model_dump(mode="json") for item in artifacts],
            "jsonArtifacts": contents,
        }

    def list_mapping_knowledge(
        self,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> tuple[MappingKnowledgeEntry, ...]:
        return self._catalogue.list_mapping_knowledge(user_id=user_id)

    def _stored(self, thread_id: str, *, user_id: str) -> tuple[StoredArtifact, ...]:
        run_ids = tuple(
            run.id for run in self._catalogue.list_runs_for_thread(thread_id, user_id=user_id)
        )
        return self._catalogue.list_artifacts_for_runs(run_ids, user_id=user_id)

    @staticmethod
    def _view(artifact: StoredArtifact) -> WorkspaceArtifact:
        return WorkspaceArtifact(
            id=artifact.id,
            kind=WorkspaceView._kind(artifact.key),
            name=PurePosixPath(artifact.key).name,
            # Expose the logical workspace path, never a private storage URI/blob URL.
            relative_path=artifact.key,
            created_at=artifact.created_at,
            created_by="workflow",
            content_type=artifact.content_type,
            sha256=artifact.sha256,
            size=artifact.size,
            product_id=artifact.product_id,
            derived_from=artifact.derived_from,
        )

    @staticmethod
    def _kind(key: str) -> ArtifactKind:
        if key.startswith("sources/"):
            return ArtifactKind.RAW
        if key.startswith("evidence/"):
            return ArtifactKind.EVIDENCE
        if key.endswith("coverage.json"):
            return ArtifactKind.COVERAGE
        if key.startswith("mapping/"):
            return ArtifactKind.MAPPING
        if key.startswith("aas/"):
            return ArtifactKind.AAS
        if key.startswith("dpp/"):
            return ArtifactKind.EXPORT
        if key.startswith("research/"):
            return ArtifactKind.SEARCH
        return ArtifactKind.RAW

    @staticmethod
    def _archive_name(artifact: StoredArtifact, used: set[str]) -> str:
        parts = tuple(
            part for part in PurePosixPath(artifact.key).parts if part not in {"", ".", "..", "/"}
        )
        path = PurePosixPath(*parts) if parts else PurePosixPath(artifact.id)
        name = path.as_posix()
        if name not in used:
            return name
        suffix = path.suffix
        stem = path.name.removesuffix(suffix)
        return path.with_name(f"{stem}-{artifact.id[:17]}{suffix}").as_posix()

    @staticmethod
    def _scalar(value: object) -> str | int | float | bool | None:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

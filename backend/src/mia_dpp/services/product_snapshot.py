"""Architecture-neutral product work snapshot synchronization helpers."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from mia_dpp.canonical import sha256_json
from mia_dpp.domain.product_work import ProductWorkSnapshot, ProductWorkStage
from mia_dpp.persistence.catalogue import ProductCatalogue, ProductSnapshotConflict
from mia_dpp.runtime.run_context import RunContext


def model_fingerprint(value: BaseModel | object) -> str:
    """Return a stable fingerprint for persisted model inputs."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    return sha256_json(value)


def semantic_mapper_fingerprint(mapper: object | None) -> str | None:
    """Return a stable baseline identity for one semantic mapper implementation."""

    if mapper is None:
        return None
    mapper_type = type(mapper)
    return sha256_json({"class": f"{mapper_type.__module__}.{mapper_type.__qualname__}"})


def update_product_snapshot(
    catalogue: ProductCatalogue,
    context: RunContext,
    stage: ProductWorkStage,
    *,
    expected_version: int,
    source_generation: int,
    **changes: Any,
) -> ProductWorkSnapshot:
    """Merge authoritative stage pointers without depending on graph state."""

    catalogue.assert_run_generation(context.run_id)
    current = catalogue.get_product_work_snapshot(
        context.product_id,
        user_id=context.user_id,
    )
    if current is None and expected_version != 0:
        raise ProductSnapshotConflict(
            f"workflow expected snapshot version {expected_version}, but no snapshot exists"
        )
    if current is not None and current.version != expected_version:
        raise ProductSnapshotConflict(
            f"workflow computed from snapshot version {expected_version}, "
            f"but current version is {current.version}"
        )

    snapshot = current or ProductWorkSnapshot(
        id=f"snapshot-{context.product_id}",
        user_id=context.user_id,
        product_id=context.product_id,
        run_id=context.run_id,
        thread_id=context.thread_id,
    )
    snapshot = snapshot.model_copy(
        update={
            "run_id": context.run_id,
            "thread_id": context.thread_id,
            "workflow_stage": stage,
            "source_generation": source_generation,
            **changes,
        }
    )
    return catalogue.save_product_work_snapshot(
        snapshot,
        expected_version=expected_version,
    )

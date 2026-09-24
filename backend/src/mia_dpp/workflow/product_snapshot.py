"""Keep the product-level snapshot synchronized with durable workflow artifacts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from mia_dpp.canonical import sha256_json
from mia_dpp.domain.product_work import ProductWorkSnapshot, ProductWorkStage
from mia_dpp.workflow.workspace import RunWorkspace


def model_fingerprint(value: BaseModel | object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    return sha256_json(value)


def semantic_mapper_fingerprint(mapper: object | None) -> str | None:
    """Stable baseline identity; concrete mappers may later expose richer configuration."""

    if mapper is None:
        return None
    mapper_type = type(mapper)
    return sha256_json({"class": f"{mapper_type.__module__}.{mapper_type.__qualname__}"})


def update_product_snapshot(
    work: RunWorkspace,
    stage: ProductWorkStage,
    **changes: Any,
) -> ProductWorkSnapshot:
    """Merge authoritative stage pointers without discarding still-valid earlier work."""

    current = work.ctx.catalogue.get_product_work_snapshot(
        work.product_id,
        user_id=work.user_id,
    )
    snapshot = current or ProductWorkSnapshot(
        id=f"snapshot-{work.product_id}",
        user_id=work.user_id,
        product_id=work.product_id,
        run_id=work.run_id,
        thread_id=work.state["thread_id"],
    )
    snapshot = snapshot.model_copy(
        update={
            "run_id": work.run_id,
            "thread_id": work.state["thread_id"],
            "workflow_stage": stage,
            **changes,
        }
    )
    return work.ctx.catalogue.save_product_work_snapshot(
        snapshot,
        expected_version=current.version if current is not None else 0,
    )

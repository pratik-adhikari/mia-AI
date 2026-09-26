"""LangGraph compatibility adapter for architecture-neutral product snapshots."""

from __future__ import annotations

from typing import Any

from mia_dpp.domain.product_work import ProductWorkSnapshot, ProductWorkStage
from mia_dpp.runtime.run_context import RunContext
from mia_dpp.services.product_snapshot import (
    model_fingerprint,
    semantic_mapper_fingerprint,
    update_product_snapshot as _update_product_snapshot,
)
from mia_dpp.workflow.workspace import RunWorkspace


def update_product_snapshot(
    work: RunWorkspace,
    stage: ProductWorkStage,
    **changes: Any,
) -> ProductWorkSnapshot:
    """Translate graph state into the neutral snapshot synchronization contract."""

    return _update_product_snapshot(
        work.ctx.catalogue,
        RunContext.from_mapping(work.state),
        stage,
        expected_version=int(work.state.get("product_snapshot_version", 0)),
        source_generation=int(work.state.get("source_generation", 0)),
        **changes,
    )


__all__ = [
    "model_fingerprint",
    "semantic_mapper_fingerprint",
    "update_product_snapshot",
]

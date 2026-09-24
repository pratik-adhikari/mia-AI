"""Rules for deciding whether a previous human review is still valid."""

from __future__ import annotations

from enum import StrEnum

from mia_dpp.domain.product_work import ProductWorkSnapshot


class ReviewReuseStatus(StrEnum):
    CURRENT = "current"
    UNKNOWN = "unknown"
    STALE = "stale"


def review_reuse_status(
    snapshot: ProductWorkSnapshot | None,
    *,
    evidence_fingerprint: str | None,
    target_fingerprint: str | None,
) -> ReviewReuseStatus:
    """Human review survives technical failures but not changed evidence/template assumptions."""

    if snapshot is None or snapshot.reviewed_mapping_artifact_id is None:
        return ReviewReuseStatus.UNKNOWN
    if (
        snapshot.reviewed_evidence_fingerprint is None
        or snapshot.reviewed_target_fingerprint is None
    ):
        return ReviewReuseStatus.UNKNOWN
    if (
        snapshot.reviewed_evidence_fingerprint == evidence_fingerprint
        and snapshot.reviewed_target_fingerprint == target_fingerprint
    ):
        return ReviewReuseStatus.CURRENT
    return ReviewReuseStatus.STALE

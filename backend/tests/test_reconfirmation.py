from mia_dpp.domain.product_work import ProductWorkSnapshot
from mia_dpp.services.reconfirmation import ReviewReuseStatus, review_reuse_status


def _snapshot(**updates):
    base = ProductWorkSnapshot(
        id="snapshot-reconfirm",
        user_id="user-a",
        product_id="product-a",
        run_id="run-a",
        thread_id="thread-a",
        reviewed_mapping_artifact_id="mapping-a",
    )
    return base.model_copy(update=updates)


def test_current_review_survives_when_evidence_and_targets_match() -> None:
    snapshot = _snapshot(
        reviewed_evidence_fingerprint="evidence-v1",
        reviewed_target_fingerprint="targets-v1",
    )
    assert (
        review_reuse_status(
            snapshot,
            evidence_fingerprint="evidence-v1",
            target_fingerprint="targets-v1",
        )
        is ReviewReuseStatus.CURRENT
    )


def test_review_becomes_stale_when_source_or_template_assumptions_change() -> None:
    snapshot = _snapshot(
        reviewed_evidence_fingerprint="evidence-v1",
        reviewed_target_fingerprint="targets-v1",
    )
    assert (
        review_reuse_status(
            snapshot,
            evidence_fingerprint="evidence-v2",
            target_fingerprint="targets-v1",
        )
        is ReviewReuseStatus.STALE
    )
    assert (
        review_reuse_status(
            snapshot,
            evidence_fingerprint="evidence-v1",
            target_fingerprint="targets-v2",
        )
        is ReviewReuseStatus.STALE
    )


def test_legacy_review_without_fingerprints_is_reused_with_confirmation() -> None:
    assert (
        review_reuse_status(
            _snapshot(),
            evidence_fingerprint="evidence-v1",
            target_fingerprint="targets-v1",
        )
        is ReviewReuseStatus.UNKNOWN
    )

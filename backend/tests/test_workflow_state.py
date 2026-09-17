"""Small graph-state invariants replacing legacy agent/session-store tests."""

from mia_dpp.workflow.state import reset_product_state


def test_product_reset_clears_run_scoped_state_but_keeps_next_url() -> None:
    reset = reset_product_state(
        product_url="https://example.com/next",
        product_queue=("product-next",),
    )
    assert reset["product_url"] == "https://example.com/next"
    assert reset["product_queue"] == ("product-next",)
    assert reset["run_id"] == ""
    assert reset["evidence_artifact_id"] == ""
    assert reset["review_required"] is False
    assert reset["research_attempts"] == 0

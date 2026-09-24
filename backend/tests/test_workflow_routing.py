from mia_dpp.workflow.routing import (
    after_background_integration,
    after_coverage,
    after_product_lookup,
    after_research,
)


def test_product_cache_short_circuits_workflow() -> None:
    assert after_product_lookup({"cache_hit": True}) == "reuse"
    assert after_product_lookup({"cache_hit": False}) == "extract"


def test_gaps_research_then_fall_back_to_human_input() -> None:
    assert after_coverage({"required_unresolved": 1, "research_attempts": 0}) == "research"
    assert (
        after_coverage(
            {"required_unresolved": 1, "research_attempts": 2, "max_research_attempts": 2}
        )
        == "human"
    )
    assert after_coverage({"required_unresolved": 0}) == "build"
    assert after_research({"research_found_source": True}) == "extract"
    assert after_research({"research_found_source": False}) == "human"



def test_reuse_mode_does_not_change_completed_dpp_short_circuit() -> None:
    assert after_product_lookup({"cache_hit": True, "reuse_mode": "reuse_completed_dpp"}) == "reuse"
    assert after_product_lookup({"cache_hit": False, "reuse_mode": "continue_saved_work"}) == "extract"



def test_background_integration_reopens_review_only_when_new_conflicts_exist() -> None:
    assert after_background_integration({"review_required": True}) == "review"
    assert after_background_integration({"review_required": False}) == "coverage"

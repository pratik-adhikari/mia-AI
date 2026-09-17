from mia_dpp.workflow.routing import (
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

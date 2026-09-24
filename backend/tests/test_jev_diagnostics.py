"""Distribution-derived diagnostics remain model-agnostic and inspectable."""

from __future__ import annotations

from mia_dpp.semantic.diagnostics import choice_diagnostics, normalized_entropy


def test_choice_diagnostics_capture_margin_ratio_and_argmax() -> None:
    diagnostics = choice_diagnostics(
        question_id="route",
        selected_choice="technical_data",
        probabilities={
            "technical_data": 0.85,
            "digital_nameplate": 0.07,
            "other": 0.04,
            "__unresolved__": 0.04,
        },
    )

    assert diagnostics.selected_probability == 0.85
    assert diagnostics.top_probability == 0.85
    assert diagnostics.runner_up_probability == 0.07
    assert diagnostics.margin == 0.78
    assert round(diagnostics.runner_up_ratio, 4) == round(0.07 / 0.85, 4)
    assert diagnostics.choice_is_argmax is True
    assert 0.0 <= diagnostics.normalized_entropy <= 1.0


def test_entropy_is_zero_for_deterministic_distribution() -> None:
    assert normalized_entropy({"only": 1.0}) == 0.0

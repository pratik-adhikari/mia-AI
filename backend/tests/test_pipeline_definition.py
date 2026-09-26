"""Contracts for replaceable orchestration and future GUI-composed pipelines."""

import pytest

from mia_dpp.orchestration.pipeline import PipelineDefinition


def test_pipeline_definition_accepts_known_components() -> None:
    definition = PipelineDefinition.model_validate(
        {
            "schema_version": 1,
            "id": "local-private",
            "privacy_mode": "local",
            "steps": [
                {"id": "extract", "component": "extract.web"},
                {
                    "id": "normalize",
                    "component": "normalize.evidence",
                    "after": ["extract"],
                },
                {
                    "id": "build",
                    "component": "aas.build",
                    "after": ["normalize"],
                },
            ],
        }
    )

    assert definition.id == "local-private"
    assert definition.privacy_mode == "local"
    assert definition.steps[-1].after == ("normalize",)


def test_pipeline_definition_rejects_unknown_component() -> None:
    with pytest.raises(ValueError, match="unknown component"):
        PipelineDefinition.model_validate(
            {
                "id": "invalid",
                "steps": [{"id": "mystery", "component": "does.not.exist"}],
            }
        )


def test_pipeline_definition_rejects_unknown_dependency() -> None:
    with pytest.raises(ValueError, match="depends on unknown steps"):
        PipelineDefinition.model_validate(
            {
                "id": "invalid",
                "steps": [
                    {
                        "id": "normalize",
                        "component": "normalize.evidence",
                        "after": ["missing"],
                    }
                ],
            }
        )

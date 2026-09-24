from dataclasses import dataclass

from pydantic import BaseModel

from mia_dpp.workflow.product_snapshot import model_fingerprint, semantic_mapper_fingerprint


class FixtureModel(BaseModel):
    alpha: int
    beta: str


def test_model_fingerprint_is_stable_and_changes_with_content() -> None:
    first = model_fingerprint(FixtureModel(alpha=1, beta="x"))
    same = model_fingerprint(FixtureModel(alpha=1, beta="x"))
    changed = model_fingerprint(FixtureModel(alpha=2, beta="x"))

    assert first == same
    assert first != changed


@dataclass
class FixtureMapper:
    pass


def test_semantic_mapper_fingerprint_identifies_mapper_implementation() -> None:
    assert semantic_mapper_fingerprint(FixtureMapper()) == semantic_mapper_fingerprint(FixtureMapper())
    assert semantic_mapper_fingerprint(None) is None

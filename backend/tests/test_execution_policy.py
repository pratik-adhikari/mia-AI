"""Privacy and cost policy stays separate from business capabilities."""

from mia_dpp.capabilities.catalog import component_catalog
from mia_dpp.orchestration.execution_policy import (
    ExecutionPolicy,
    ExecutionTier,
    PrivacyMode,
)


def test_deterministic_component_never_requires_a_model() -> None:
    component = component_catalog()["aas.build"]
    policy = ExecutionPolicy(
        privacy_mode=PrivacyMode.SHAREABLE,
        allow_advanced_remote=True,
    )

    assert policy.allowed_tiers(component) == (ExecutionTier.DETERMINISTIC,)


def test_local_mode_never_exposes_remote_models() -> None:
    component = component_catalog()["mapping.resolve"]
    policy = ExecutionPolicy(privacy_mode=PrivacyMode.LOCAL)

    assert policy.allowed_tiers(component) == (ExecutionTier.LOCAL_MODEL,)


def test_shareable_mode_can_offer_advanced_remote_intelligence() -> None:
    component = component_catalog()["mapping.resolve"]
    policy = ExecutionPolicy(
        privacy_mode=PrivacyMode.SHAREABLE,
        allow_advanced_remote=True,
    )

    assert policy.allowed_tiers(component) == (
        ExecutionTier.LOCAL_MODEL,
        ExecutionTier.REMOTE_STANDARD,
        ExecutionTier.REMOTE_ADVANCED,
    )

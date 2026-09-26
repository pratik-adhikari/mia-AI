"""Privacy/cost policy for choosing how a reusable capability may execute."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from mia_dpp.capabilities.catalog import ComponentSpec, ExecutionRequirement


class PrivacyMode(StrEnum):
    LOCAL = "local"
    HYBRID = "hybrid"
    SHAREABLE = "shareable"


class ExecutionTier(StrEnum):
    DETERMINISTIC = "deterministic"
    LOCAL_MODEL = "local_model"
    REMOTE_STANDARD = "remote_standard"
    REMOTE_ADVANCED = "remote_advanced"


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    """Bound execution choices without embedding provider/model names in capabilities."""

    privacy_mode: PrivacyMode = PrivacyMode.LOCAL
    allow_advanced_remote: bool = False

    def allowed_tiers(self, component: ComponentSpec) -> tuple[ExecutionTier, ...]:
        if component.execution is ExecutionRequirement.DETERMINISTIC:
            return (ExecutionTier.DETERMINISTIC,)

        local = (ExecutionTier.LOCAL_MODEL,)
        if self.privacy_mode is PrivacyMode.LOCAL:
            if component.execution is ExecutionRequirement.REMOTE_MODEL_REQUIRED:
                return ()
            return local

        remote = (ExecutionTier.REMOTE_STANDARD,)
        if self.allow_advanced_remote:
            remote = (*remote, ExecutionTier.REMOTE_ADVANCED)

        if component.execution is ExecutionRequirement.REMOTE_MODEL_REQUIRED:
            return remote

        return (*local, *remote)

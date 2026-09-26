"""Versioned runtime policy for selecting orchestration without changing business code."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ArchitectureConfig:
    architecture: str = "graph-v1"
    schema_version: int = 1

    @classmethod
    def load(cls, path: Path) -> "ArchitectureConfig":
        payload: dict[str, Any] = json.loads(path.read_text())
        return cls(
            architecture=str(payload.get("architecture", "graph-v1")),
            schema_version=int(payload.get("schemaVersion", 1)),
        )

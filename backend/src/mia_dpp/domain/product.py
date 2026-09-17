"""Persistent product, workflow-run, conversation, and DPP catalogue concepts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, Field

from mia_dpp.domain.base import WireModel, utc_now


class RunStatus(StrEnum):
    RUNNING = "running"
    AWAITING_HUMAN = "awaiting_human"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    FAILED = "failed"
    REUSED = "reused"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ProductRecord(WireModel):
    id: str
    canonical_url: str
    original_url: str
    manufacturer: str | None = None
    name: str | None = None
    manufacturer_product_id: str | None = None
    image_url: str | None = None
    image_artifact_id: str | None = None
    created_at: AwareDatetime = Field(default_factory=utc_now)
    updated_at: AwareDatetime = Field(default_factory=utc_now)
    last_verified_at: AwareDatetime | None = None


class ProductRun(WireModel):
    id: str
    product_id: str
    thread_id: str
    status: RunStatus = RunStatus.RUNNING
    refresh_requested: bool = False
    reused_from_run_id: str | None = None
    started_at: AwareDatetime = Field(default_factory=utc_now)
    finished_at: AwareDatetime | None = None
    error: str | None = None
    metrics: dict[str, int | float | str | bool | None] = Field(default_factory=dict)


class ChatMessage(WireModel):
    id: str
    thread_id: str
    run_id: str | None = None
    role: MessageRole
    content: str = Field(min_length=1)
    timestamp: AwareDatetime = Field(default_factory=utc_now)


class RunEvent(WireModel):
    id: str
    run_id: str
    event_type: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    summary: str
    timestamp: AwareDatetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DppVersion(WireModel):
    id: str
    product_id: str
    run_id: str
    version: int = Field(ge=1)
    dpp_artifact_id: str
    aas_artifact_id: str | None = None
    validation_artifact_id: str | None = None
    source_fingerprint: str | None = None
    deployable: bool = False
    created_at: AwareDatetime = Field(default_factory=utc_now)

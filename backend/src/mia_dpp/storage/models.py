"""Durable object metadata shared by local and production artifact stores."""

from pydantic import AwareDatetime, Field

from mia_dpp.domain.base import WireModel, utc_now


class StoredArtifact(WireModel):
    id: str
    key: str
    content_type: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    storage_uri: str
    product_id: str | None = None
    run_id: str | None = None
    derived_from: tuple[str, ...] = ()
    created_at: AwareDatetime = Field(default_factory=utc_now)

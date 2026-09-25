"""AAS idShort normalization shared by compilation and semantic proposals."""

from __future__ import annotations

import re

ID_SHORT_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def sanitize_id_short(
    value: str,
    *,
    fallback: str = "Product",
    max_length: int = 128,
) -> str:
    """Return a deterministic AAS-safe idShort without changing source evidence."""

    if max_length < 1:
        raise ValueError("max_length must be at least one")
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"{fallback}_{cleaned}" if cleaned else fallback
    cleaned = cleaned[:max_length]
    if not ID_SHORT_PATTERN.fullmatch(cleaned):
        raise ValueError(f"could not construct valid idShort from {value!r}")
    return cleaned

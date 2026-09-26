"""Stable product URL parsing helpers used by workflow entry points."""

from __future__ import annotations

import re

from mia_dpp.domain.product_identity import canonical_product_url

_URL = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)


def direct_product_url(message: str) -> str | None:
    """Return the first explicit product URL from a chat turn, if one exists."""

    match = _URL.search(message)
    return match.group(0).rstrip(".,;") if match else None


__all__ = ["canonical_product_url", "direct_product_url"]

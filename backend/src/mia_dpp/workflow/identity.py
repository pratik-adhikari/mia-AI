"""Stable product identity helpers."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = {"fbclid", "gclid", "msclkid"}
_URL = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)


def canonical_product_url(url: str) -> str:
    """Normalize a public product URL for durable cache identity."""

    parsed = urlsplit(url.strip())
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("product URL must be an absolute HTTP(S) URL")
    host = parsed.hostname.casefold().removeprefix("www.")
    port = parsed.port
    netloc = host if port is None else f"{host}:{port}"
    path = parsed.path.rstrip("/") or "/"
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.casefold() not in _TRACKING_KEYS
            and not key.casefold().startswith(_TRACKING_PREFIXES)
        )
    )
    return urlunsplit((parsed.scheme.casefold(), netloc, path, query, ""))


def direct_product_url(message: str) -> str | None:
    """Return the first explicit product URL from a chat turn, if one exists."""

    match = _URL.search(message)
    return match.group(0).rstrip(".,;") if match else None

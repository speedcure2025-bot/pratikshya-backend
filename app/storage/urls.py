"""
app/storage/urls.py — application-level media URL construction.

The one rule of Phase 6 media delivery: the frontend only ever receives an
application-level URL. It never sees `C:\\…`, `D:\\…` or
`/srv/media/…`, and it never has to know whether the bytes live on the
local disk, in S3 or behind a CDN.

Two shapes are produced:

  · no CDN configured (this phase)
        {API_V1_PREFIX}{MEDIA_URL_PREFIX}/{object_key}
        → /api/v1/media/objects/products/PF-W-SAR-SIL-0001/primary.avif

  · CDN configured (future)
        {MEDIA_CDN_BASE_URL}/{object_key}
        → https://cdn.example.com/products/PF-W-SAR-SIL-0001/primary.avif

Both are recognised by `is_media_url()`, so swapping providers or turning a
CDN on is a configuration change and no product component has to change.
"""

from __future__ import annotations

from typing import Optional

from app.config import settings
from app.storage.base import InvalidObjectKeyError
from app.storage.keys import normalize_object_key


def media_url_prefix() -> str:
    """`/api/v1/media/objects` (no trailing slash)."""
    return settings.media_url_prefix_absolute


def cdn_base_url() -> Optional[str]:
    """Configured CDN origin, or None."""
    return settings.media_cdn_base_url


def build_media_url(object_key: str) -> str:
    """
    Canonical URL for a validated object key.

    Raises InvalidObjectKeyError for unsafe keys so a bad key can never be
    turned into a URL a browser would fetch.
    """
    safe_key = normalize_object_key(object_key)
    cdn = cdn_base_url()
    if cdn:
        return f"{cdn}/{safe_key}"
    return f"{media_url_prefix()}/{safe_key}"


def is_media_url(value: str) -> bool:
    """True when `value` already looks like a canonical media URL."""
    text = str(value or "").strip()
    if not text:
        return False
    prefix = media_url_prefix()
    if prefix and (text == prefix or text.startswith(prefix + "/")):
        return True
    cdn = cdn_base_url()
    if cdn and text.startswith(cdn + "/"):
        return True
    return False


def object_key_from_media_url(value: str) -> Optional[str]:
    """
    Recover the object key from a canonical media URL, or None.

    Used to accept a previously issued media URL wherever an object key is
    expected, without ever trusting the caller to have validated it.
    """
    text = str(value or "").strip()
    if not text:
        return None

    prefix = media_url_prefix()
    candidate: Optional[str] = None
    if prefix and text.startswith(prefix + "/"):
        candidate = text[len(prefix) + 1:]
    else:
        cdn = cdn_base_url()
        if cdn and text.startswith(cdn + "/"):
            candidate = text[len(cdn) + 1:]

    if candidate is None:
        return None
    try:
        return normalize_object_key(candidate.split("?", 1)[0].split("#", 1)[0])
    except InvalidObjectKeyError:
        return None

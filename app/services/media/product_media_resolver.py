"""
app/services/media/product_media_resolver.py — canonical product image URLs.

The rule this module enforces: the BACKEND decides what a product's image
reference resolves to. The frontend never derives a storage path, never
builds `/images/${slug}.jpg`, and never learns which provider is active.

Resolution order for a stored reference
---------------------------------------
1. Empty                      → `""` (the UI renders its empty plate; no
                                 placeholder is ever invented)
2. Absolute URL / data URL    → verbatim (already browser-reachable)
3. Canonical media URL        → verbatim (already issued by this layer)
4. Object key or legacy
   `/images/...` public path  → mapped to the object key and, **only if the
                                 object actually exists in the configured
                                 store**, returned as the canonical media URL
5. Anything that did not
   resolve                   → the original reference, unchanged

Step 5 is the dual-read compatibility policy (report §15/§17). During the
migration the storefront keeps serving `/images/...` from `public/`, so
there is no broken-image window; once the object exists locally the same
product starts returning `/api/v1/media/objects/...`. Nothing is hidden:
`explain()` reports exactly which branch was taken, and
`GET /media/references/resolve` exposes the same decision over the API.

Resolution NEVER invents. A missing object is never swapped for a different
product's image, a placeholder, or another file.

`MEDIA_RESOLVE_PRODUCT_IMAGES=false` restores the pre-Phase-6 behaviour
(references passed through verbatim) without a code change.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Optional

from app.config import settings
from app.core.logging import get_logger
from app.storage import (
    InvalidObjectKeyError,
    StorageError,
    get_storage_provider,
    is_media_url,
    normalize_object_key,
    object_key_from_media_url,
)

logger = get_logger("app.services.media.resolver")

#: The legacy Vite `public/` prefix that real product assets still use.
LEGACY_PUBLIC_IMAGE_PREFIX = "/images/"

#: Resolution outcomes, surfaced verbatim by the API and by tests.
RESOLVED = "resolved"                 # canonical media URL to the object store
LEGACY_FALLBACK = "legacy-fallback"   # object not present yet → old path kept
PASSTHROUGH = "passthrough"           # already canonical / absolute / unknown
EMPTY = "empty"
DISABLED = "disabled"

_CACHE_LIMIT = 4096
_cache: "OrderedDict[str, str]" = OrderedDict()
_explain_cache: "OrderedDict[str, Dict[str, str]]" = OrderedDict()


def clear_resolution_cache() -> None:
    """Drop cached decisions (after a migration run or a config change)."""
    _cache.clear()
    _explain_cache.clear()


def _remember(key: str, value: str) -> str:
    _cache[key] = value
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_LIMIT:
        _cache.popitem(last=False)
    return value


def _remember_explain(key: str, value: Dict[str, str]) -> Dict[str, str]:
    _explain_cache[key] = value
    _explain_cache.move_to_end(key)
    while len(_explain_cache) > _CACHE_LIMIT:
        _explain_cache.popitem(last=False)
    return value


def _is_absolute_or_inline(value: str) -> bool:
    lowered = value.lower()
    return (
        lowered.startswith("http://")
        or lowered.startswith("https://")
        or lowered.startswith("data:")
        or lowered.startswith("blob:")
        or lowered.startswith("//")
    )


def candidate_object_key(reference: str) -> Optional[str]:
    """
    The object key a reference WOULD map to, or None if it cannot map.

    Accepts:
      · `/images/products/x/y.avif`   → `products/x/y.avif`
      · `products/x/y.avif`           → `products/x/y.avif`
      · `/api/v1/media/objects/...`   → the embedded key
    """
    text = str(reference or "").strip()
    if not text:
        return None

    from_url = object_key_from_media_url(text)
    if from_url:
        return from_url

    if text.startswith(LEGACY_PUBLIC_IMAGE_PREFIX):
        candidate = text[len(LEGACY_PUBLIC_IMAGE_PREFIX):]
    elif text.startswith("/"):
        # Any other absolute server path is not ours to reinterpret.
        return None
    else:
        candidate = text

    candidate = candidate.split("?", 1)[0].split("#", 1)[0].strip("/")
    if not candidate:
        return None
    try:
        return normalize_object_key(candidate)
    except InvalidObjectKeyError:
        return None


def explain(reference: Any, *, storage=None) -> Dict[str, str]:
    """
    Full resolution decision for one reference.

    Returns `{ reference, status, url, objectKey }`. `status` is one of the
    module constants — this is the observable half of the fallback policy.
    """
    raw = "" if reference is None else str(reference).strip()
    if not raw:
        return {"reference": "", "status": EMPTY, "url": "", "objectKey": ""}

    cached = _explain_cache.get(raw)
    if cached is not None:
        _explain_cache.move_to_end(raw)
        return cached

    if not settings.MEDIA_RESOLVE_PRODUCT_IMAGES:
        return _remember_explain(
            raw,
            {"reference": raw, "status": DISABLED, "url": raw, "objectKey": ""},
        )

    if _is_absolute_or_inline(raw) or is_media_url(raw):
        return _remember_explain(
            raw,
            {"reference": raw, "status": PASSTHROUGH, "url": raw, "objectKey": ""},
        )

    key = candidate_object_key(raw)
    if key is None:
        # A media-register id (`pm-…`) or anything unrecognised. Without the
        # media tables we cannot resolve it, and we never guess.
        return _remember_explain(
            raw,
            {"reference": raw, "status": PASSTHROUGH, "url": raw, "objectKey": ""},
        )

    provider = storage or get_storage_provider()
    try:
        if provider.object_exists(key):
            return _remember_explain(
                raw,
                {
                    "reference": raw,
                    "status": RESOLVED,
                    "url": provider.url_for(key),
                    "objectKey": key,
                },
            )
    except (InvalidObjectKeyError, StorageError) as exc:
        logger.debug("Media resolution skipped for %r: %s", raw, exc)

    # Object not present (yet). Keep the original reference so the storefront
    # keeps rendering the asset from `public/images` — documented, observable,
    # and never substituted with a different image.
    return _remember_explain(
        raw,
        {
            "reference": raw,
            "status": LEGACY_FALLBACK,
            "url": raw,
            "objectKey": key,
        },
    )


def resolve_product_image_reference(reference: Any, *, storage=None) -> str:
    """
    Resolve one product image reference to a browser-usable URL.

    This is what `ProductService._to_storefront` / `_to_admin` call, so the
    API contract keeps its existing field names while the VALUES become
    canonical media URLs wherever the object store can serve them.
    """
    raw = "" if reference is None else str(reference).strip()
    if not raw:
        return ""
    cached = _cache.get(raw)
    if cached is not None:
        _cache.move_to_end(raw)
        return cached
    return _remember(raw, explain(raw, storage=storage)["url"])


def resolve_product_image_list(
    references: Optional[Iterable[Any]],
    *,
    storage=None,
) -> List[str]:
    """Resolve a gallery list, preserving order and dropping empties."""
    if not references:
        return []
    resolved = [
        resolve_product_image_reference(item, storage=storage)
        for item in references
    ]
    return [item for item in resolved if item]


def resolve_many(references: Iterable[Any], *, storage=None) -> List[Dict[str, str]]:
    """Batch `explain()` — backs `POST /media/references/resolve`."""
    return [explain(item, storage=storage) for item in (references or [])]


__all__ = [
    "LEGACY_PUBLIC_IMAGE_PREFIX",
    "RESOLVED",
    "LEGACY_FALLBACK",
    "PASSTHROUGH",
    "EMPTY",
    "DISABLED",
    "candidate_object_key",
    "clear_resolution_cache",
    "explain",
    "resolve_product_image_reference",
    "resolve_product_image_list",
    "resolve_many",
]

"""
app/storage — the single object-storage abstraction (Phase 6).

    StorageProvider
        ├── LocalStorageProvider   ← active (no AWS, Docker, Redis or Celery)
        └── S3StorageProvider      ← interface-ready, needs real credentials

Callers use `get_storage_provider()` and nothing else. Switching providers
is a `STORAGE_PROVIDER` configuration change; no service, route or frontend
module has to know which one is active.
"""

from __future__ import annotations

from typing import Optional

from app.config import settings
from app.storage.base import (
    InvalidObjectKeyError,
    ObjectCollisionError,
    ObjectMetadata,
    ObjectNotFoundError,
    StorageError,
    StorageProvider,
    StorageProviderNotConfigured,
    StoredObject,
)
from app.storage.keys import (
    ALLOWED_NAMESPACES,
    NAMESPACE_COLLECTIONS,
    NAMESPACE_HERO,
    NAMESPACE_MARKETING,
    NAMESPACE_PRODUCTS,
    NAMESPACE_UPLOADS,
    is_safe_object_key,
    normalize_object_key,
    object_key_for_namespace,
    product_object_key,
    sanitize_filename,
    sanitize_key_segment,
    validate_product_id,
)
from app.storage.local import (
    LocalStorageProvider,
    content_type_for_key,
    sha256_bytes,
    sha256_file,
    sha256_stream,
)
from app.storage.s3 import S3StorageProvider
from app.storage.urls import (
    build_media_url,
    is_media_url,
    media_url_prefix,
    object_key_from_media_url,
)

__all__ = [
    # errors
    "StorageError",
    "ObjectNotFoundError",
    "InvalidObjectKeyError",
    "StorageProviderNotConfigured",
    "ObjectCollisionError",
    # value objects / interface
    "StorageProvider",
    "ObjectMetadata",
    "StoredObject",
    # providers
    "LocalStorageProvider",
    "S3StorageProvider",
    # keys
    "ALLOWED_NAMESPACES",
    "NAMESPACE_PRODUCTS",
    "NAMESPACE_COLLECTIONS",
    "NAMESPACE_HERO",
    "NAMESPACE_MARKETING",
    "NAMESPACE_UPLOADS",
    "normalize_object_key",
    "is_safe_object_key",
    "sanitize_filename",
    "sanitize_key_segment",
    "validate_product_id",
    "product_object_key",
    "object_key_for_namespace",
    # urls
    "build_media_url",
    "is_media_url",
    "media_url_prefix",
    "object_key_from_media_url",
    # hashing / content type
    "content_type_for_key",
    "sha256_bytes",
    "sha256_file",
    "sha256_stream",
    # factory
    "get_storage_provider",
    "reset_storage_provider",
    "create_storage_provider",
    "storage_status",
]


_PROVIDER_CACHE: Optional[StorageProvider] = None


def create_storage_provider(active_settings=None) -> StorageProvider:
    """
    Build a provider from settings. No caching — used by tests and by the
    migration CLI, which may point at a different root than the app.
    """
    cfg = active_settings or settings
    provider_name = (
        cfg.storage_provider_name
        if hasattr(cfg, "storage_provider_name")
        else str(getattr(cfg, "STORAGE_PROVIDER", "local") or "local").strip().lower()
    )

    if provider_name == "local":
        return LocalStorageProvider(cfg.local_media_root_path)

    if provider_name == "s3":
        # Raises StorageProviderNotConfigured until real credentials exist.
        return S3StorageProvider(
            bucket=getattr(cfg, "AWS_BUCKET_NAME", "") or "",
            region=getattr(cfg, "AWS_REGION", "") or "",
            access_key_id=getattr(cfg, "AWS_ACCESS_KEY_ID", None),
            secret_access_key=getattr(cfg, "AWS_SECRET_ACCESS_KEY", None),
            endpoint_url=getattr(cfg, "AWS_ENDPOINT_URL", None),
        )

    raise StorageProviderNotConfigured(
        f"Unknown STORAGE_PROVIDER '{provider_name}'. Supported: 'local', 's3'."
    )


def get_storage_provider() -> StorageProvider:
    """Process-wide provider instance, built once from settings."""
    global _PROVIDER_CACHE
    if _PROVIDER_CACHE is None:
        _PROVIDER_CACHE = create_storage_provider()
    return _PROVIDER_CACHE


def reset_storage_provider() -> None:
    """
    Drop the cached provider.

    Used by tests that change `LOCAL_MEDIA_ROOT` / `STORAGE_PROVIDER` and by
    any future configuration reload.
    """
    global _PROVIDER_CACHE
    _PROVIDER_CACHE = None


def storage_status() -> dict:
    """
    Non-secret storage summary for `GET /media/storage/status`.

    Includes the provider name, whether it is usable, the application-level
    URL prefix, and (for S3) the bucket name. Never credentials, never an
    absolute filesystem path.
    """
    provider_name = settings.storage_provider_name
    try:
        provider = get_storage_provider()
        detail = provider.describe()
        return {
            "ok": True,
            "provider": provider_name,
            "configured": True,
            "detail": detail,
            "urlPrefix": media_url_prefix(),
            "cdnConfigured": bool(settings.media_cdn_base_url),
            "namespaces": list(ALLOWED_NAMESPACES),
            "resolveProductImages": bool(settings.MEDIA_RESOLVE_PRODUCT_IMAGES),
        }
    except StorageProviderNotConfigured as exc:
        return {
            "ok": False,
            "provider": provider_name,
            "configured": False,
            "detail": {"error": str(exc)},
            "urlPrefix": media_url_prefix(),
            "cdnConfigured": bool(settings.media_cdn_base_url),
            "namespaces": list(ALLOWED_NAMESPACES),
            "resolveProductImages": bool(settings.MEDIA_RESOLVE_PRODUCT_IMAGES),
        }

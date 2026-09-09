"""
app/services/media/product_media_records.py — registered product-media read model (Phase 7).

Phase 7 introduced durable records: `MediaAssetModel` (the verified object in
the configured store) and `ProductMediaModel` (the ordered product ↔ media
mapping, source of truth for NEW product associations).

This module is the ONE async reader the rest of the application uses to
answer "which registered media does this product have, in which order, and
what URL does each resolve to?" — so the media router, the product
projections and the storefront never hand-roll the join themselves.

Design rules:

  · provider abstraction only — a URL is built with the same
    `build_media_url` helper the object routes use, so the resolution is
    identical for `STORAGE_PROVIDER=local` today and `=s3` later. Nothing
    here touches a filesystem path.
  · legacy products are untouched — a product with no registered rows simply
    gets an empty list; callers keep dual-reading the legacy columns.
  · ordering is deterministic — the primary image first, then `sort_order`,
    then insertion order as a stable tie-break.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.media.media_asset import MediaAssetModel
from app.models.media.product_media import ProductMediaModel
from app.storage import build_media_url

#: Role that marks the cover/primary image for a product.
PRIMARY_ROLE = "COVER"


def _order_key(item: Dict[str, Any]) -> tuple:
    # Primary first, then authored sort order, then association id for a
    # stable, deterministic sequence.
    return (0 if item["isPrimary"] else 1, item["sortOrder"], item["assignmentId"])


def serialise_assignment(
    mapping: ProductMediaModel, asset: MediaAssetModel
) -> Dict[str, Any]:
    """One product ↔ media association in the API's camelCase envelope."""
    return {
        "assignmentId": str(mapping.id),
        "mediaId": str(asset.id),
        "objectKey": asset.object_key,
        "url": build_media_url(asset.object_key),
        "mimeType": asset.mime_type,
        "mediaType": asset.media_type,
        "title": asset.title,
        "altText": asset.alt_text,
        "fileSize": asset.file_size,
        "status": asset.status,
        "role": mapping.role or "gallery",
        "sortOrder": mapping.sort_order or 0,
        "isPrimary": bool(mapping.is_primary),
        "assignedBy": mapping.assigned_by,
    }


async def registered_media_for_product(
    db: AsyncSession, product_id: str
) -> List[Dict[str, Any]]:
    """
    The product's registered media associations (new source of truth),
    ordered primary-first then sort_order.

    Returns [] when the product has no registered media — the legacy columns
    keep serving that product exactly as before (dual-read).
    """
    if not product_id:
        return []
    stmt = (
        select(ProductMediaModel, MediaAssetModel)
        .join(MediaAssetModel, MediaAssetModel.id == ProductMediaModel.media_id)
        .where(ProductMediaModel.product_id == product_id)
    )
    rows = (await db.execute(stmt)).all()
    items = [serialise_assignment(mapping, asset) for mapping, asset in rows]
    items.sort(key=_order_key)
    return items


async def registered_media_for_products(
    db: AsyncSession, product_ids: Iterable[str]
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Bulk variant of `registered_media_for_product` — ONE query for a whole
    page of products, so list surfaces never degrade into N+1 reads.
    """
    ids = [str(product_id) for product_id in dict.fromkeys(product_ids or []) if product_id]
    if not ids:
        return {}
    stmt = (
        select(ProductMediaModel, MediaAssetModel)
        .join(MediaAssetModel, MediaAssetModel.id == ProductMediaModel.media_id)
        .where(ProductMediaModel.product_id.in_(ids))
    )
    rows = (await db.execute(stmt)).all()
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for mapping, asset in rows:
        grouped.setdefault(str(mapping.product_id), []).append(
            serialise_assignment(mapping, asset)
        )
    for bucket in grouped.values():
        bucket.sort(key=_order_key)
    return grouped


def primary_item(items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The primary association of an already-ordered list, if any."""
    if not items:
        return None
    for item in items:
        if item.get("isPrimary"):
            return item
    return items[0]


def gallery_urls(items: List[Dict[str, Any]]) -> List[str]:
    """Canonical URLs for the registered gallery, in display order."""
    return [item["url"] for item in items if item.get("url")]


__all__ = [
    "PRIMARY_ROLE",
    "serialise_assignment",
    "registered_media_for_product",
    "registered_media_for_products",
    "primary_item",
    "gallery_urls",
]

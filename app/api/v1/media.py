"""
Media — API router (Phase 6).

URL mapping:

  Public (no auth)
  ─────────────────────────────────────────────────────────────────────────
  GET    /media/health                              ← module health
  GET    /media/storage/status                      ← provider summary, no secrets
  GET    /media/objects/{object_key}                ← serve one object (bytes)
  HEAD   /media/objects/{object_key}                ← headers only
  GET    /media/object-meta/{object_key}            ← size / type / SHA-256
  POST   /media/references/resolve                  ← batch reference → URL decisions
  GET    /media/products/{product_id}/media-set     ← resolved media set for a product

  Admin (RBAC — existing media.* permission vocabulary)
  ─────────────────────────────────────────────────────────────────────────
  POST   /media/objects                             ← upload (media.upload)
  POST   /media/products/{product_id}/objects       ← upload for a product (media.upload)
  DELETE /media/objects/{object_key}                ← delete one object (media.delete)

Security notes
--------------
  · There is no `?path=` style endpoint. The only path-like input is an
    OBJECT KEY, validated by `app.storage.keys.normalize_object_key`
    (no traversal, no backslashes, no absolute/drive forms, namespace
    allow-list, character allow-list) before any I/O. A request cannot reach
    a file outside `LOCAL_MEDIA_ROOT`.
  · Error responses never include a filesystem path — only the object key
    the caller supplied.
  · Mutations sit behind the Phase-1 admin guard plus the fine-grained
    `media.upload` / `media.delete` permissions. Frontend visibility is not
    the control.
  · Uploads are validated by content signature, not by filename or by the
    client's `Content-Type` header.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import FileResponse, StreamingResponse

from app.core.exceptions import (
    BusinessLogicException,
    ConflictException,
    NotFoundException,
)
from app.core.logging import get_logger
from app.config import settings
from app.dependencies import (
    get_current_admin,
    get_db,
    require_admin_permission,
)
from app.models.auth.user import UserModel
from app.models.catalog.product import ProductModel
from app.models.media.media_asset import MediaAssetModel
from app.models.media.product_media import ProductMediaModel
from app.storage import get_storage_provider
from sqlalchemy import update
from app.schemas.media.media import (
    DEFAULT_PRODUCT_MEDIA_ROLE,
    MEDIA_UPLOAD_NAMESPACES,
    PRODUCT_MEDIA_ROLE_VALUES,
    MediaAssetListResponse,
    MediaObjectMetaResponse,
    MediaObjectResponse,
    MediaReferenceResolveRequest,
    MediaReferenceResolveResponse,
    MediaRegistrationResponse,
    MediaStorageStatusResponse,
    ProductMediaSetResponse,
    coerce_product_media_role,
)
from app.api.v1.response_docs import canonical_error_responses
from app.services.media.media_service import MediaService
from app.services.media.media_validation import MediaValidationError
from app.services.media.product_media_records import (
    primary_item,
    registered_media_for_product,
)
from app.services.media.product_media_resolver import (
    resolve_many,
    resolve_product_image_list,
    resolve_product_image_reference,
)
from app.services.media.upload_service import UploadService
from app.storage import InvalidObjectKeyError, ObjectCollisionError, ObjectNotFoundError

logger = get_logger("app.api.v1.media")

router = APIRouter(prefix="/media", tags=["Media Assets"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_media_service(db: Optional[AsyncSession] = None) -> MediaService:
    return MediaService(db)


def _invalid_key(message: str) -> BusinessLogicException:
    """422 for an unusable object key. Never echoes a filesystem path."""
    return BusinessLogicException(message)


def _safe_key(media: MediaService, raw: str) -> str:
    try:
        return media.coerce_object_key(raw)
    except InvalidObjectKeyError as exc:
        raise _invalid_key(f"Invalid media object key: {exc}") from exc


def _object_key_for_product(
    media: MediaService, filename: str, product_id: str
) -> str:
    try:
        return media.object_key_for_upload(
            filename, namespace="products", product_id=product_id
        )
    except InvalidObjectKeyError as exc:
        raise _invalid_key(str(exc)) from exc


# ---------------------------------------------------------------------------
# Public reads
# ---------------------------------------------------------------------------

@router.get("/health", summary="Module health check")
async def health_check():
    return {"module": "media", "status": "active"}


@router.get(
    "/storage/status",
    response_model=MediaStorageStatusResponse,
    summary="Storage provider summary (no secrets)",
)
async def storage_status():
    """
    Which provider is active, the application-level media URL prefix, and
    whether a CDN is configured. Credentials and filesystem paths are never
    part of this payload.
    """
    return MediaService.storage_status()


@router.post(
    "/references/resolve",
    response_model=MediaReferenceResolveResponse,
    summary="Resolve product image references to canonical media URLs",
)
async def resolve_references(payload: MediaReferenceResolveRequest):
    """
    Batch resolution decisions.

    Each entry reports `status` — `resolved`, `legacy-fallback`,
    `passthrough`, `empty` or `disabled` — so the migration's dual-read
    behaviour is observable instead of silent.
    """
    entries = resolve_many(payload.references)
    return {"ok": True, "items": entries, "total": len(entries)}


@router.get(
    "/objects/{object_key:path}",
    summary="Serve one media object",
    responses={200: {"content": {"image/*": {}, "video/*": {}, "application/octet-stream": {}}}},
)
async def get_media_object(object_key: str, request: Request):
    """
    Return the bytes of a single object inside the configured media store.

    404 when the key is legal but absent; 422 when the key is not legal.
    """
    media = _get_media_service()
    key = _safe_key(media, object_key)

    try:
        metadata = await run_in_threadpool(media.object_metadata, key)
    except ObjectNotFoundError as exc:
        raise NotFoundException("Media object not found.") from exc
    except InvalidObjectKeyError as exc:
        raise _invalid_key(str(exc)) from exc

    headers = {
        "ETag": f'"{metadata.etag or metadata.checksum_sha256}"',
        "Last-Modified": metadata.last_modified or "",
        "X-Content-Type-Options": "nosniff",
        # Objects are cacheable but not immutable: a key can legitimately be
        # replaced by an admin, so a long immutable TTL would serve stale art.
        "Cache-Control": "public, max-age=3600",
    }
    if not headers["Last-Modified"]:
        headers.pop("Last-Modified")

    provider = media.storage
    if hasattr(provider, "local_path"):
        try:
            path = await run_in_threadpool(provider.local_path, key)
        except ObjectNotFoundError as exc:
            raise NotFoundException("Media object not found.") from exc
        return FileResponse(str(path), media_type=metadata.content_type, headers=headers)

    stream = await run_in_threadpool(media.open_object, key)
    return StreamingResponse(stream, media_type=metadata.content_type, headers=headers)


@router.head("/objects/{object_key:path}", summary="Media object headers only")
async def head_media_object(object_key: str):
    media = _get_media_service()
    key = _safe_key(media, object_key)
    try:
        metadata = await run_in_threadpool(media.object_metadata, key)
    except ObjectNotFoundError as exc:
        raise NotFoundException("Media object not found.") from exc
    except InvalidObjectKeyError as exc:
        raise _invalid_key(str(exc)) from exc
    return Response(
        status_code=200,
        media_type=metadata.content_type,
        headers={
            "Content-Length": str(metadata.size),
            "ETag": f'"{metadata.etag or metadata.checksum_sha256}"',
        },
    )


@router.get(
    "/object-meta/{object_key:path}",
    response_model=MediaObjectMetaResponse,
    summary="Media object metadata",
)
async def get_media_object_meta(object_key: str):
    media = _get_media_service()
    key = _safe_key(media, object_key)
    try:
        metadata = await run_in_threadpool(media.object_metadata, key)
    except ObjectNotFoundError as exc:
        raise NotFoundException("Media object not found.") from exc
    except InvalidObjectKeyError as exc:
        raise _invalid_key(str(exc)) from exc
    return {
        "ok": True,
        "object": metadata.as_dict(),
        "url": media.object_url(key),
    }


@router.get(
    "/products/{product_id}/media-set",
    response_model=ProductMediaSetResponse,
    summary="Resolved media set for one product",
)
async def get_product_media_set(
    product_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    The product's media set — BOTH halves of the Phase 7 dual-read.

    Legacy half (unchanged): the product's own authored columns (`image`,
    `hover_image`, `additional_images`) resolved through the storage layer.

    Registered half (Phase 7 source of truth for NEW media): the durable
    `media_product_media` associations joined to their `media_media_asset`
    rows, ordered primary-first — each with its object key and canonical
    `/media/objects/...` URL. A product with no registered rows answers
    exactly as it did before this phase.
    """
    result = await db.execute(
        select(ProductModel).where(ProductModel.id == product_id)
    )
    product = result.scalars().first()
    if not product:
        raise NotFoundException(f"Product '{product_id}' not found.")

    primary = resolve_product_image_reference(product.image)
    hover = resolve_product_image_reference(product.hover_image)
    gallery = resolve_product_image_list(product.additional_images)
    # A gallery that repeats the cover is not a gallery.
    gallery = [item for item in gallery if item and item != primary] or list(gallery)

    registered = await registered_media_for_product(db, product.id)
    registered_primary = primary_item(registered)

    return {
        "ok": True,
        "productId": product.id,
        "primary": primary or None,
        "hover": hover or None,
        "gallery": gallery,
        "primaryMediaId": product.primary_media_id,
        "mediaIds": product.media_ids or [],
        "galleryMediaIds": product.gallery_media_ids or [],
        "mediaItems": registered,
        "primaryMediaUrl": registered_primary["url"] if registered_primary else None,
        "mediaRecordsAvailable": bool(registered),
        "note": (
            "Registered media records answer this product (mediaItems; "
            "primary-first). Legacy authored columns remain resolved for "
            "compatibility."
            if registered
            else "No registered media records for this product yet — the "
            "legacy authored image columns are resolved for compatibility."
        ),
    }


# ---------------------------------------------------------------------------
# Admin mutations (RBAC)
# ---------------------------------------------------------------------------

@router.post(
    "/objects",
    response_model=MediaObjectResponse,
    status_code=201,
    summary="Upload a media object (admin)",
)
async def upload_media_object(
    file: UploadFile = File(..., description="Image file to store"),
    namespace: str = Form(
        "products",
        description=(
            "Storage namespace. Closed vocabulary, enforced by "
            "`app.storage.keys` — a key whose first segment is not one of "
            "these is rejected before any I/O, which is what stops this "
            "route becoming a generic file server over the storage root. "
            "Case-sensitive. `products` additionally requires `productId`."
        ),
        json_schema_extra={"enum": list(MEDIA_UPLOAD_NAMESPACES)},
    ),
    product_id: Optional[str] = Form(None, alias="productId"),
    group: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_admin),
):
    """
    Store one image in the object store and return its canonical URL.

    The object key is derived from real identity (`products/{PRODUCT_ID}/
    {filename}`), never from a random temp name. Nothing is written to the
    database: persisting the reference onto a product uses the existing
    `PATCH /admin/products/{id}` media fields.

    `namespace` is validated against the closed storage vocabulary
    (`app.storage.keys.ALLOWED_NAMESPACES`); an unknown namespace is a 422
    `BUSINESS_RULE_VIOLATION` and no object is written.
    """
    await require_admin_permission(current_user, db, "media.upload")

    upload = UploadService(db)
    try:
        stored = await upload.store_upload(
            file_obj=file,
            filename=file.filename,
            declared_content_type=file.content_type,
            namespace=namespace,
            product_id=product_id,
            group=group,
        )
    except MediaValidationError as exc:
        raise BusinessLogicException(str(exc)) from exc
    except ObjectCollisionError as exc:
        raise ConflictException(str(exc)) from exc
    except InvalidObjectKeyError as exc:
        raise _invalid_key(str(exc)) from exc

    return {"ok": True, "object": stored, "status": 201}


@router.post(
    "/products/{product_id}/objects",
    response_model=MediaObjectResponse,
    status_code=201,
    summary="Upload a media object for a product (admin)",
)
async def upload_product_media_object(
    product_id: str,
    file: UploadFile = File(..., description="Image file to store"),
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_admin),
):
    """Upload scoped to one product — the key namespace cannot be spoofed."""
    await require_admin_permission(current_user, db, "media.upload")

    result = await db.execute(
        select(ProductModel.id).where(ProductModel.id == product_id)
    )
    if result.scalars().first() is None:
        raise NotFoundException(f"Product '{product_id}' not found.")

    upload = UploadService(db)
    try:
        stored = await upload.store_upload(
            file_obj=file,
            filename=file.filename,
            declared_content_type=file.content_type,
            namespace="products",
            product_id=product_id,
        )
    except MediaValidationError as exc:
        raise BusinessLogicException(str(exc)) from exc
    except ObjectCollisionError as exc:
        raise ConflictException(str(exc)) from exc
    except InvalidObjectKeyError as exc:
        raise _invalid_key(str(exc)) from exc

    return {"ok": True, "object": stored, "status": 201}


@router.post(
    "/register",
    response_model=MediaRegistrationResponse,
    status_code=201,
    summary="Register an uploaded object as a media asset",
    responses=canonical_error_responses(401, 403, 404, 422, 500),
)
async def register_media_object(
    object_key: str = Form(...), product_id: Optional[str] = Form(None),
    role: str = Form(
        DEFAULT_PRODUCT_MEDIA_ROLE,
        description=(
            "Product-media role. Closed vocabulary (plan §24 step 9): "
            + ", ".join(PRODUCT_MEDIA_ROLE_VALUES)
            + ". Matched case-insensitively and stored with the caller's own "
            "casing; an empty value stores the default "
            f"'{DEFAULT_PRODUCT_MEDIA_ROLE}'. Anything else is rejected with "
            "422 BUSINESS_RULE_VIOLATION."
        ),
    ),
    sort_order: int = Form(0), is_primary: bool = Form(False), title: Optional[str] = Form(None),
    alt_text: Optional[str] = Form(None), db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_admin),
):
    """Create the durable row only after verifying the real object exists.

    Idempotent by object key: re-registering the same key returns the same
    asset row and UPDATES the existing product association's role / sort
    order / primary flag instead of duplicating it. When `is_primary` is set,
    every other association of the same product is demoted in the same
    transaction — a product never has two primaries.

    `role` is checked against the declared product-media vocabulary before
    anything is written. Before plan §24 step 9 this column accepted any
    string: the value went straight into a `String(30)` column, so a long
    role was an HTTP 500 (`StringDataRightTruncation`) on PostgreSQL rather
    than a validation rejection.
    """
    await require_admin_permission(current_user, db, "media.upload")
    try:
        role = coerce_product_media_role(role)
    except ValueError as exc:
        raise BusinessLogicException(str(exc)) from exc
    media = _get_media_service(db); key = _safe_key(media, object_key)
    try: meta = await run_in_threadpool(media.object_metadata, key)
    except ObjectNotFoundError as exc: raise NotFoundException("Media object not found.") from exc
    row = (await db.execute(select(MediaAssetModel).where(MediaAssetModel.object_key == key))).scalars().first()
    if row is None:
        row = MediaAssetModel(object_key=key, storage_provider=settings.storage_provider_name,
            media_type="image", mime_type=meta.content_type, original_filename=key.rsplit("/",1)[-1],
            file_size=meta.size, checksum_sha256=meta.checksum_sha256, status="uploaded", scope="product",
            uploaded_by=current_user.id, title=title, alt_text=alt_text)
        db.add(row); await db.flush()
    mapping = None
    if product_id:
        product = (await db.execute(select(ProductModel).where(ProductModel.id == product_id))).scalars().first()
        if product is None: raise NotFoundException("Product not found.")
        if is_primary:
            await db.execute(update(ProductMediaModel).where(ProductMediaModel.product_id == product_id).values(is_primary=False))
        mapping = (await db.execute(select(ProductMediaModel).where(ProductMediaModel.product_id == product_id, ProductMediaModel.media_id == row.id))).scalars().first()
        if mapping is None:
            mapping = ProductMediaModel(product_id=product_id, media_id=row.id, role=role, sort_order=sort_order, is_primary=is_primary, assigned_by=current_user.id)
            db.add(mapping)
        else: mapping.role, mapping.sort_order, mapping.is_primary = role, sort_order, is_primary
        await db.commit()
        # The storefront product DTO is cached; the registered read model
        # changed, so the cached snapshot must not outlive this write.
        try:
            from app.services.catalog.product_service import ProductService

            await ProductService(db).invalidate_product_cache(product.id, product.slug)
        except Exception:  # cache maintenance must never fail a committed write
            logger.debug("Product media cache invalidation skipped", exc_info=True)
    else: await db.commit()
    return {
        "ok": True,
        "media": {
            "id": row.id,
            "objectKey": row.object_key,
            "url": media.object_url(row.object_key),
            "mimeType": row.mime_type,
            "title": row.title,
            "altText": row.alt_text,
            "status": row.status,
        },
        "assigned": bool(product_id),
        "assignment": (
            {
                "productId": mapping.product_id,
                "mediaId": mapping.media_id,
                "role": mapping.role,
                "sortOrder": mapping.sort_order,
                "isPrimary": mapping.is_primary,
            }
            if mapping is not None
            else None
        ),
    }


@router.get(
    "/assets",
    response_model=MediaAssetListResponse,
    summary="List registered media assets",
    responses=canonical_error_responses(401, 403, 500),
)
async def list_media_assets(db: AsyncSession = Depends(get_db), current_user: UserModel = Depends(get_current_admin)):
    await require_admin_permission(current_user, db, "media.upload")
    rows = (await db.execute(select(MediaAssetModel).order_by(MediaAssetModel.created_at.desc()))).scalars().all()
    return {"ok": True, "items": [{"id": r.id, "objectKey": r.object_key, "url": MediaService(db).object_url(r.object_key), "status": r.status, "mimeType": r.mime_type} for r in rows]}


@router.delete(
    "/objects/{object_key:path}",
    summary="Delete one media object (admin)",
)
async def delete_media_object(
    object_key: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_admin),
):
    """
    Delete exactly one explicitly named object.

    There is no cascade and no garbage collection in this phase: an object is
    only removed when an administrator names it. The original
    `frontend/public/images` assets are outside the storage root and can
    never be reached from here.
    """
    await require_admin_permission(current_user, db, "media.delete")

    media = _get_media_service(db)
    key = _safe_key(media, object_key)
    deleted = await run_in_threadpool(media.delete_object, key)
    if not deleted:
        raise NotFoundException("Media object not found.")
    return {"ok": True, "deleted": True, "key": key}

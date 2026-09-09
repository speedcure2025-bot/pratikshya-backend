"""
Media Pydantic schemas (Phase 6).

Envelope convention matches the rest of the API: `{ ok: true, … }`.

Only the object-storage contract is modelled here. Media *records*
(`media_media_asset` rows, product↔media mappings, review rows) are NOT
modelled because those tables declare no business columns in the existing
schema, and Phase 6 may not invent them.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from app.storage.keys import ALLOWED_NAMESPACES


# ---------------------------------------------------------------------------
# Declared vocabularies (plan §24 step 9 — API-085/086/125/126/132/133/140)
# ---------------------------------------------------------------------------
#
# ROLE — `media_product_media.role`.
#
# This vocabulary is DERIVED, not invented. It is the frontend's own declared
# product-media role set (`frontend/src/config/mediaTypes.js`
# `PRODUCT_MEDIA_ROLES`), which is the only place in the system where the
# vocabulary was ever written down. The backend already agreed with one member
# of it (`product_media_records.PRIMARY_ROLE = "COVER"`).
#
# Before this declaration the column accepted ANY string: `role` was
# `Form("gallery")` and was written straight through to a `String(30)` column.
# A 200-character role was accepted on SQLite and would raise
# `StringDataRightTruncation` — an HTTP 500 for what is a validation
# rejection — on PostgreSQL.
#
# CASE: membership is tested case-INSENSITIVELY and the caller's own casing is
# preserved. The system genuinely uses both casings today — the backend
# defaults to lowercase `"gallery"` in four places (the `Form` default, the
# column default, `RegisteredProductMediaItem.role` and the
# `serialise_assignment` fallback) while the frontend sends `"COVER"` from
# `PRODUCT_MEDIA_COVER_ROLE`. Folding to one canonical case would rewrite what
# callers store, which is a data-shape decision this step was not asked to
# make, and it is recorded as a finding instead. Membership is what the plan
# asked to close, and closing it does not require picking a winner.

PRODUCT_MEDIA_ROLE_VALUES: Tuple[str, ...] = (
    "COVER",
    "GALLERY",
    "DETAIL",
    "LIFESTYLE",
    "MODEL",
    "CLOSEUP",
    "PRODUCT_VIDEO",
    "SHOWCASE",
    "DETAIL_VIDEO",
    "LIFESTYLE_VIDEO",
)

#: What `POST /media/register` stores when the caller names no role. This is
#: the pre-existing `Form(...)` / column default and is deliberately unchanged.
DEFAULT_PRODUCT_MEDIA_ROLE = "gallery"

#: Lookup set for the case-insensitive membership test.
_PRODUCT_MEDIA_ROLE_LOOKUP = {value.casefold() for value in PRODUCT_MEDIA_ROLE_VALUES}

#: NAMESPACE — re-exported from the storage layer, which is the ONE place that
#: decides what a legal object key looks like. It is not redeclared here: a
#: second copy would be free to drift from the copy that actually enforces.
MEDIA_UPLOAD_NAMESPACES: Tuple[str, ...] = tuple(ALLOWED_NAMESPACES)


def product_media_role_error(role: str) -> str:
    """The canonical rejection message for a role outside the vocabulary."""
    return (
        f"Media role '{role}' is not a recognised product media role. "
        f"Allowed roles: {', '.join(PRODUCT_MEDIA_ROLE_VALUES)}."
    )


def is_product_media_role(role: str) -> bool:
    """True when `role` names a declared product-media role (any casing)."""
    return str(role or "").strip().casefold() in _PRODUCT_MEDIA_ROLE_LOOKUP


def coerce_product_media_role(role: Optional[str]) -> str:
    """
    Validate one product-media role and return the value to store.

    Whitespace is trimmed; the caller's casing is preserved. An empty or
    whitespace-only role falls back to the pre-existing default rather than
    being rejected, which is exactly what the route did before this step —
    tightening that would be a behaviour change nobody asked for.

    Raises `ValueError` for a role outside the vocabulary; the route turns
    that into the canonical 422 business-rule envelope. No new error code is
    introduced.
    """
    text = str(role or "").strip()
    if not text:
        return DEFAULT_PRODUCT_MEDIA_ROLE
    if text.casefold() not in _PRODUCT_MEDIA_ROLE_LOOKUP:
        raise ValueError(product_media_role_error(text))
    return text


# ---------------------------------------------------------------------------
# Legacy placeholders (retained — previously declared, still unused)
# ---------------------------------------------------------------------------

class MediaBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MediaCreate(MediaBase):
    pass


class MediaResponse(MediaBase):
    id: str


# ---------------------------------------------------------------------------
# Object storage (Phase 6)
# ---------------------------------------------------------------------------

class MediaObjectMetadata(BaseModel):
    """Provider-independent object metadata. No paths, no secrets."""

    model_config = ConfigDict(populate_by_name=True)

    key: str
    size: int = 0
    content_type: str = Field("application/octet-stream", alias="contentType")
    checksum_sha256: str = Field("", alias="checksumSha256")
    last_modified: Optional[str] = Field(None, alias="lastModified")
    etag: Optional[str] = None
    provider: str = ""


class MediaObjectPayload(BaseModel):
    """Descriptor returned after a successful upload."""

    model_config = ConfigDict(populate_by_name=True)

    key: str
    url: str
    created: bool = True
    already_exists: bool = Field(False, alias="alreadyExists")
    size: int = 0
    content_type: str = Field("application/octet-stream", alias="contentType")
    checksum_sha256: str = Field("", alias="checksumSha256")


class MediaObjectResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    object: MediaObjectPayload
    status: int = 201


class MediaRegistrationAsset(BaseModel):
    """The registered media descriptor returned by ``POST /media/register``."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    object_key: str = Field(..., alias="objectKey")
    url: str
    mime_type: str = Field(..., alias="mimeType")
    title: Optional[str]
    alt_text: Optional[str] = Field(None, alias="altText")
    status: str


class MediaRegistrationAssignment(BaseModel):
    """The optional product association returned during media registration."""

    model_config = ConfigDict(populate_by_name=True)

    product_id: str = Field(..., alias="productId")
    media_id: str = Field(..., alias="mediaId")
    role: str
    sort_order: int = Field(..., alias="sortOrder")
    is_primary: bool = Field(..., alias="isPrimary")


class MediaRegistrationResponse(BaseModel):
    """Successful wire shape for ``POST /media/register``."""

    model_config = ConfigDict(populate_by_name=True)

    ok: bool
    media: MediaRegistrationAsset
    assigned: bool
    assignment: Optional[MediaRegistrationAssignment]


class MediaAssetListItem(BaseModel):
    """One item in the unpaginated registered media library."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    object_key: str = Field(..., alias="objectKey")
    url: str
    status: str
    mime_type: str = Field(..., alias="mimeType")


class MediaAssetListResponse(BaseModel):
    """Successful wire shape for ``GET /media/assets``."""

    model_config = ConfigDict(populate_by_name=True)

    ok: bool
    items: List[MediaAssetListItem]


class MediaObjectMetaResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    object: MediaObjectMetadata
    url: str = ""


class MediaStorageStatusResponse(BaseModel):
    """Non-secret provider summary."""

    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    provider: str = "local"
    configured: bool = True
    detail: Dict[str, Any] = {}
    url_prefix: str = Field("", alias="urlPrefix")
    cdn_configured: bool = Field(False, alias="cdnConfigured")
    namespaces: List[str] = []
    resolve_product_images: bool = Field(True, alias="resolveProductImages")


class MediaReferenceResolveRequest(BaseModel):
    """Batch of product image references to resolve."""

    references: List[str] = []


class MediaReferenceDecision(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reference: str = ""
    status: str = "empty"
    url: str = ""
    object_key: str = Field("", alias="objectKey")


class MediaReferenceResolveResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    items: List[MediaReferenceDecision] = []
    total: int = 0


class RegisteredProductMediaItem(BaseModel):
    """
    One durable product ↔ media association (Phase 7 read model).

    The `url` is the canonical media URL built from the registered object's
    key through the configured storage provider — it is what a storefront or
    an admin surface renders for a NEW product-media association.
    """

    model_config = ConfigDict(populate_by_name=True)

    assignment_id: str = Field("", alias="assignmentId")
    media_id: str = Field("", alias="mediaId")
    object_key: str = Field("", alias="objectKey")
    url: str = ""
    mime_type: str = Field("", alias="mimeType")
    media_type: str = Field("image", alias="mediaType")
    title: Optional[str] = None
    alt_text: Optional[str] = Field(None, alias="altText")
    file_size: int = Field(0, alias="fileSize")
    status: str = ""
    role: str = "gallery"
    sort_order: int = Field(0, alias="sortOrder")
    is_primary: bool = Field(False, alias="isPrimary")
    assigned_by: Optional[str] = Field(None, alias="assignedBy")


class ProductMediaSetResponse(BaseModel):
    """
    Resolved media set for a product.

    `primary` / `hover` / `gallery` keep resolving the product's own legacy
    authored columns (dual-read compatibility for the pre-Phase-7 catalogue),
    while `mediaItems` reports the durable registered associations
    (Phase 7 source of truth for NEW product media). `mediaRecordsAvailable`
    tells the caller which half answers this product.
    """

    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    product_id: str = Field("", alias="productId")
    primary: Optional[str] = None
    hover: Optional[str] = None
    gallery: List[str] = []
    primary_media_id: Optional[str] = Field(None, alias="primaryMediaId")
    media_ids: List[str] = Field([], alias="mediaIds")
    gallery_media_ids: List[str] = Field([], alias="galleryMediaIds")
    media_items: List[RegisteredProductMediaItem] = Field([], alias="mediaItems")
    primary_media_url: Optional[str] = Field(None, alias="primaryMediaUrl")
    media_records_available: bool = Field(False, alias="mediaRecordsAvailable")
    note: str = ""

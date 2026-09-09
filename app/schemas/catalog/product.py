"""
Product Pydantic schemas.

Mirrors the full shape from PRODUCT_CATALOGUE_SPEC.md §3 and API_CONTRACT.md.
Envelope convention: { ok: true, ... } or { ok: false, error / errors }.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ── Enums / literal constants ────────────────────────────────────────────────

PRODUCT_STATUS_VALUES = ("DRAFT", "PENDING_REVIEW", "PUBLISHED", "ARCHIVED")
REVIEW_STATE_VALUES = ("NONE", "PENDING", "APPROVED", "REJECTED")

# ── Lifecycle transitions (plan §9.1/§9.2, §24 step 8) ───────────────────────
#
# `status` (visibility) and `review.state` (approval) are two INDEPENDENT axes.
# Every lifecycle action below declares the source states it accepts on the axis
# that actually governs it, so a guard can never disagree with the documented
# contract.  This is a DECLARATION of the transitions the services already
# implement — not a new state machine, and not a new lifecycle.
#
# `None` means "this axis is not consulted by this action".

LIFECYCLE_TRANSITIONS: Dict[str, Dict[str, Any]] = {
    # action:        from_status,                       from_review_state,   to_status
    "submitReview": {
        "from_status": ("DRAFT",),
        "from_review": ("NONE", "REJECTED"),
        "to_status": "PENDING_REVIEW",
        "to_review": "PENDING",
    },
    "approve": {
        # Approval is a REVIEW verb: it is governed by the review axis, but a
        # product that has left PENDING_REVIEW (archived, unpublished, already
        # live) is no longer under review and must not be approvable.
        "from_status": ("PENDING_REVIEW",),
        "from_review": ("PENDING", "APPROVED"),  # APPROVED ⇒ idempotent no-op
        "to_status": None,                       # approve NEVER changes status
        "to_review": "APPROVED",
        # Re-approving an already-approved product returns 200 and writes
        # nothing (plan §9.2 "Idempotent when already approved").
        "idempotent_when": "already_approved",
    },
    "reject": {
        "from_status": ("PENDING_REVIEW",),
        "from_review": ("PENDING",),
        "to_status": "DRAFT",
        "to_review": "REJECTED",
    },
    "publish": {
        # Publication is a VISIBILITY verb gated on the review axis. DRAFT is a
        # legal source so an unpublished or restored product that still carries
        # an APPROVED review can go live again without a second review round —
        # the behaviour plan §9.2 specifies ("gated on review.state == APPROVED").
        "from_status": ("DRAFT", "PENDING_REVIEW", "PUBLISHED"),
        "from_review": ("APPROVED",),
        "to_status": "PUBLISHED",
        "to_review": None,
        # Re-publishing a product that is already live returns 200 and writes
        # nothing (plan §9.2 "Idempotent when already live"). This short-circuit
        # runs BEFORE the review check, so an already-live row is never failed
        # for a review state it can no longer be in.
        "idempotent_when": "already_live",
    },
    "unpublish": {
        "from_status": ("PUBLISHED",),
        "from_review": None,
        "to_status": "DRAFT",
        "to_review": None,
    },
    "archive": {
        "from_status": ("DRAFT", "PENDING_REVIEW", "PUBLISHED"),
        "from_review": None,
        "to_status": "ARCHIVED",
        "to_review": None,
    },
    "restore": {
        "from_status": ("ARCHIVED",),
        "from_review": None,
        "to_status": "DRAFT",
        "to_review": None,
    },
}

# ── Review-flag vocabulary (plan §9.2 "declare the flag vocabulary") ─────────
#
# Mirrors the canonical frontend declaration in
# `frontend/src/services/productReviewFlags.js`, plus `KIDS_MIGRATION_REVIEW`,
# which only the backend blocking set has ever used.  Flags are REVIEW SIGNALS,
# never a second status system.

REVIEW_FLAG_BLOCKING = (
    "NAME_REVIEW_REQUIRED",
    "PRICE_REVIEW_REQUIRED",
    "TAXONOMY_REVIEW_REQUIRED",
    "GROUP_REVIEW_REQUIRED",
    "VARIANT_REVIEW_REQUIRED",
    "NEEDS_MEDIA",
    "MEDIA_OWNERSHIP_REVIEW",
    "CONFLICT_UNRESOLVED",
    "KIDS_MIGRATION_REVIEW",
)
REVIEW_FLAG_INFORMATIONAL = (
    "CONFLICT_REVIEW_LATER",
    "MEDIA_OWNERSHIP_MOVED",
    "MEDIA_UNASSIGNED",
)
REVIEW_FLAG_VALUES = REVIEW_FLAG_BLOCKING + REVIEW_FLAG_INFORMATIONAL
SORT_ALIASES: Dict[str, str] = {
    "price-low": "price-asc",
    "price-high": "price-desc",
    "name": "name-asc",
    "az": "name-asc",
}
VALID_SORTS = {
    "recommended", "newest", "price-asc", "price-desc",
    "discount", "name-asc", "popularity", "rating",
}
# Admin catalogue sorts — matched 1:1 by ProductService.list_admin_products.
ADMIN_SORTS = {
    "newest", "oldest", "name", "price-asc", "price-desc", "status", "updated",
}
# Permanent product IDs are the `catalog_product.id` primary key
# (String(36)).  The canonical frontend identity
# `PF-{DEPT}-{FAMILY}-{NNNN}` (e.g. "PF-K-BYS-TSH-0001") fits this width,
# so the same format is accepted on create-draft and change-id without any
# schema change.  Characters stay uppercase alphanumeric + dash.
PRODUCT_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,35}$")


# ── Nested sub-schemas ────────────────────────────────────────────────────────

class PricingDetail(BaseModel):
    mrp: int = 0
    selling_price: int = Field(0, alias="sellingPrice")
    discount_type: str = Field("none", alias="discountType")
    discount_value: float = Field(0.0, alias="discountValue")
    tax_mode: str = Field("INCLUSIVE", alias="taxMode")
    tax_rate: float = Field(0.0, alias="taxRate")
    custom_tax_rate: Optional[float] = Field(None, alias="customTaxRate")

    model_config = ConfigDict(populate_by_name=True)


class ReviewDetail(BaseModel):
    state: str = "NONE"
    submitted_by: Optional[str] = Field(None, alias="submittedBy")
    submitted_at: Optional[str] = Field(None, alias="submittedAt")
    reviewed_by: Optional[str] = Field(None, alias="reviewedBy")
    reviewed_at: Optional[str] = Field(None, alias="reviewedAt")
    rejection_reason: str = Field("", alias="rejectionReason")

    model_config = ConfigDict(populate_by_name=True)


class SeoDetail(BaseModel):
    title: str = ""
    description: str = ""


class ReturnPolicy(BaseModel):
    eligibility: str = ""
    window: str = ""
    notes: str = ""


class FacetValue(BaseModel):
    value: str
    label: str
    count: int


class FacetCounts(BaseModel):
    category: List[FacetValue] = []
    subcategory: List[FacetValue] = []
    gender: List[FacetValue] = []
    price: List[FacetValue] = []
    size: List[FacetValue] = []
    color: List[FacetValue] = []
    fabric: List[FacetValue] = []
    material: List[FacetValue] = []
    occasion: List[FacetValue] = []
    collection: List[FacetValue] = []
    rating: List[FacetValue] = []
    availability: List[FacetValue] = []


# ── Storefront (customer-facing) product ─────────────────────────────────────

class StorefrontProduct(BaseModel):
    """Public projection returned to customers — toStorefrontProduct()."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    product_id: str = Field(alias="productId", default="")
    name: str = ""
    slug: str = ""
    sku: str = ""
    brand: str = "Pratikshya Fashon"
    product_type: str = Field("fashion", alias="productType")
    category: str = ""
    subcategory: str = ""
    gender: str = "Women"

    short_description: str = Field("", alias="shortDescription")
    description: str = ""
    highlights: List[Any] = []
    care_instructions: List[Any] = Field([], alias="careInstructions")
    delivery_info: str = Field("", alias="deliveryInfo")
    return_info: str = Field("", alias="returnInfo")
    return_policy: Optional[ReturnPolicy] = Field(None, alias="returnPolicy")

    fabric: str = ""
    material: str = ""
    primary_color: str = Field("", alias="primaryColor")
    secondary_color: str = Field("", alias="secondaryColor")
    colors: List[str] = []
    patterns: List[str] = []
    occasion: List[str] = []
    sizes: List[str] = []
    unavailable_colors: List[str] = Field([], alias="unavailableColors")
    unavailable_sizes: List[str] = Field([], alias="unavailableSizes")
    season: str = ""
    fit: str = ""
    length: str = ""

    collection: str = ""
    collections: List[str] = []
    tags: List[str] = []
    badges: List[Any] = []
    is_featured: bool = Field(False, alias="isFeatured")
    is_bestseller: bool = Field(False, alias="isBestseller")
    is_new: bool = Field(False, alias="isNew")
    is_limited_edition: bool = Field(False, alias="isLimitedEdition")
    is_trending: bool = Field(False, alias="isTrending")

    price: int = 0
    original_price: Optional[int] = Field(None, alias="originalPrice")
    currency: str = "INR"
    # Derived from pricing engine
    discount_percent: float = Field(0.0, alias="discountPercent")
    is_on_sale: bool = Field(False, alias="isOnSale")

    stock: int = 0
    availability: str = "in-stock"

    rating: Optional[float] = None
    review_count: int = Field(0, alias="reviewCount")

    # Media
    image: str = ""
    hover_image: str = Field("", alias="hoverImage")
    additional_images: List[str] = Field([], alias="additionalImages")
    primary_media_id: Optional[str] = Field(None, alias="primaryMediaId")

    href: str = ""
    status: str = "PUBLISHED"


# ── Admin product (full record) ───────────────────────────────────────────────

class AdminProduct(BaseModel):
    """Full admin record — includes review workflow fields."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    product_id: str = Field(alias="productId", default="")
    name: str = ""
    slug: str = ""
    sku: str = ""
    brand: str = "Pratikshya Fashon"
    product_type: str = Field("fashion", alias="productType")
    product_code: str = Field("", alias="productCode")
    barcode: str = ""
    internal_reference: str = Field("", alias="internalReference")

    category: str = ""
    subcategory: str = ""
    gender: str = "Women"

    short_description: str = Field("", alias="shortDescription")
    description: str = ""
    highlights: List[Any] = []
    specifications: Dict[str, Any] = {}
    care_instructions: List[Any] = Field([], alias="careInstructions")
    delivery_info: str = Field("", alias="deliveryInfo")
    return_info: str = Field("", alias="returnInfo")
    return_policy: Optional[ReturnPolicy] = Field(None, alias="returnPolicy")

    fabric: str = ""
    material: str = ""
    primary_color: str = Field("", alias="primaryColor")
    secondary_color: str = Field("", alias="secondaryColor")
    colors: List[str] = []
    patterns: List[str] = []
    work: List[str] = []
    occasion: List[str] = []
    sizes: List[str] = []
    unavailable_colors: List[str] = Field([], alias="unavailableColors")
    unavailable_sizes: List[str] = Field([], alias="unavailableSizes")
    season: str = ""
    fit: str = ""
    length: str = ""

    collection: str = ""
    collections: List[str] = []
    tags: List[str] = []
    badges: List[Any] = []
    is_featured: bool = Field(False, alias="isFeatured")
    is_bestseller: bool = Field(False, alias="isBestseller")
    is_new: bool = Field(False, alias="isNew")
    is_limited_edition: bool = Field(False, alias="isLimitedEdition")
    is_trending: bool = Field(False, alias="isTrending")
    flags: Dict[str, bool] = {}

    price: int = 0
    original_price: Optional[int] = Field(None, alias="originalPrice")
    compare_at_price: Optional[int] = Field(None, alias="compareAtPrice")
    currency: str = "INR"
    pricing: Optional[PricingDetail] = None
    price_history: List[Dict[str, Any]] = Field([], alias="priceHistory")

    stock: int = 0
    availability: str = "in-stock"
    inventory_tracked: bool = Field(False, alias="inventoryTracked")
    low_stock_threshold: int = Field(5, alias="lowStockThreshold")

    rating: Optional[float] = None
    review_count: int = Field(0, alias="reviewCount")

    seo: Optional[SeoDetail] = None

    status: str = "DRAFT"
    published: bool = False
    review: Optional[ReviewDetail] = None
    review_flags: List[str] = Field([], alias="reviewFlags")
    assigned_employee_id: Optional[str] = Field(None, alias="assignedEmployeeId")

    media_ids: List[str] = Field([], alias="mediaIds")
    primary_media_id: Optional[str] = Field(None, alias="primaryMediaId")
    gallery_media_ids: List[str] = Field([], alias="galleryMediaIds")
    image: str = ""
    hover_image: str = Field("", alias="hoverImage")
    additional_images: List[str] = Field([], alias="additionalImages")

    created_by: Optional[str] = Field(None, alias="createdBy")
    created_at: Optional[str] = Field(None, alias="createdAt")
    updated_by: Optional[str] = Field(None, alias="updatedBy")
    updated_at: Optional[str] = Field(None, alias="updatedAt")
    published_by: Optional[str] = Field(None, alias="publishedBy")
    published_at: Optional[str] = Field(None, alias="publishedAt")
    history: List[Dict[str, Any]] = []


class EmployeeProduct(BaseModel):
    """Employee-safe product projection.

    This is deliberately not an ``AdminProduct`` subclass: inheritance would
    publish the admin workflow and audit fields in OpenAPI and at runtime.
    Employee product reads contain catalogue content, operational state and
    read-only media/assignment projections, but never internal review history
    or actor/audit metadata.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    product_id: str = Field(alias="productId", default="")
    name: str = ""
    slug: str = ""
    sku: str = ""
    brand: str = "Pratikshya Fashon"
    product_type: str = Field("fashion", alias="productType")
    product_code: str = Field("", alias="productCode")
    barcode: str = ""
    internal_reference: str = Field("", alias="internalReference")

    category: str = ""
    subcategory: str = ""
    gender: str = "Women"
    short_description: str = Field("", alias="shortDescription")
    description: str = ""
    highlights: List[Any] = []
    specifications: Dict[str, Any] = {}
    care_instructions: List[Any] = Field([], alias="careInstructions")
    delivery_info: str = Field("", alias="deliveryInfo")
    return_info: str = Field("", alias="returnInfo")
    return_policy: Optional[ReturnPolicy] = Field(None, alias="returnPolicy")

    fabric: str = ""
    material: str = ""
    primary_color: str = Field("", alias="primaryColor")
    secondary_color: str = Field("", alias="secondaryColor")
    colors: List[str] = []
    patterns: List[str] = []
    work: List[str] = []
    occasion: List[str] = []
    sizes: List[str] = []
    unavailable_colors: List[str] = Field([], alias="unavailableColors")
    unavailable_sizes: List[str] = Field([], alias="unavailableSizes")
    season: str = ""
    fit: str = ""
    length: str = ""

    collection: str = ""
    collections: List[str] = []
    tags: List[str] = []
    badges: List[Any] = []
    is_featured: bool = Field(False, alias="isFeatured")
    is_bestseller: bool = Field(False, alias="isBestseller")
    is_new: bool = Field(False, alias="isNew")
    is_limited_edition: bool = Field(False, alias="isLimitedEdition")
    is_trending: bool = Field(False, alias="isTrending")
    flags: Dict[str, bool] = {}

    price: int = 0
    original_price: Optional[int] = Field(None, alias="originalPrice")
    compare_at_price: Optional[int] = Field(None, alias="compareAtPrice")
    currency: str = "INR"
    pricing: Optional[PricingDetail] = None

    stock: int = 0
    availability: str = "in-stock"
    inventory_tracked: bool = Field(False, alias="inventoryTracked")
    low_stock_threshold: int = Field(5, alias="lowStockThreshold")
    rating: Optional[float] = None
    review_count: int = Field(0, alias="reviewCount")
    seo: Optional[SeoDetail] = None

    status: str = "DRAFT"
    published: bool = False
    assigned_employee_id: Optional[str] = Field(None, alias="assignedEmployeeId")

    media_ids: List[str] = Field([], alias="mediaIds")
    primary_media_id: Optional[str] = Field(None, alias="primaryMediaId")
    gallery_media_ids: List[str] = Field([], alias="galleryMediaIds")
    image: str = ""
    hover_image: str = Field("", alias="hoverImage")
    additional_images: List[str] = Field([], alias="additionalImages")


# ── Request bodies ────────────────────────────────────────────────────────────

class ProductContentFields(BaseModel):
    """
    Admin-editable content fields for the product catalogue.

    EVERY entry here maps to an existing `catalog_product` column — this is
    the complete persistence contract for admin create/update. Fields the
    backend cannot store (variants, departments, media library records,
    SEO beyond title/description, inventory locations…) are deliberately NOT
    part of this model, so an editor payload can never half-persist or
    fabricate storage. Unknown keys are ignored (extra="ignore"), matching
    the long-standing behaviour, but nothing outside this set is written.
    """

    name: Optional[str] = None
    slug: Optional[str] = None
    sku: Optional[str] = None
    brand: Optional[str] = None
    product_type: Optional[str] = Field(None, alias="productType")
    product_code: Optional[str] = Field(None, alias="productCode")
    barcode: Optional[str] = None
    internal_reference: Optional[str] = Field(None, alias="internalReference")

    category: Optional[str] = None
    subcategory: Optional[str] = None
    gender: Optional[str] = None

    short_description: Optional[str] = Field(None, alias="shortDescription")
    description: Optional[str] = None
    highlights: Optional[List[Any]] = None
    specifications: Optional[Dict[str, Any]] = None
    care_instructions: Optional[List[Any]] = Field(None, alias="careInstructions")
    delivery_info: Optional[str] = Field(None, alias="deliveryInfo")
    return_info: Optional[str] = Field(None, alias="returnInfo")
    return_policy: Optional[Dict[str, Any]] = Field(None, alias="returnPolicy")

    fabric: Optional[str] = None
    material: Optional[str] = None
    primary_color: Optional[str] = Field(None, alias="primaryColor")
    secondary_color: Optional[str] = Field(None, alias="secondaryColor")
    colors: Optional[List[str]] = None
    patterns: Optional[List[str]] = None
    work: Optional[List[str]] = None
    occasion: Optional[List[str]] = None
    sizes: Optional[List[str]] = None
    unavailable_colors: Optional[List[str]] = Field(None, alias="unavailableColors")
    unavailable_sizes: Optional[List[str]] = Field(None, alias="unavailableSizes")
    season: Optional[str] = None
    fit: Optional[str] = None
    length: Optional[str] = None

    tags: Optional[List[str]] = None
    badges: Optional[List[Any]] = None
    is_featured: Optional[bool] = Field(None, alias="isFeatured")
    is_bestseller: Optional[bool] = Field(None, alias="isBestseller")
    is_new: Optional[bool] = Field(None, alias="isNew")
    is_limited_edition: Optional[bool] = Field(None, alias="isLimitedEdition")
    is_trending: Optional[bool] = Field(None, alias="isTrending")

    # Pricing — the server recomputes `price` from `pricing` when provided.
    price: Optional[int] = None
    original_price: Optional[int] = Field(None, alias="originalPrice")
    compare_at_price: Optional[int] = Field(None, alias="compareAtPrice")
    currency: Optional[str] = None
    pricing: Optional[Dict[str, Any]] = None

    # Stock snapshot fields — display only; no inventory system lives here.
    stock: Optional[int] = None
    availability: Optional[str] = None
    inventory_tracked: Optional[bool] = Field(None, alias="inventoryTracked")
    low_stock_threshold: Optional[int] = Field(None, alias="lowStockThreshold")

    seo: Optional[Dict[str, Any]] = None

    # Media references only — real media records/migration are a future phase.
    media_ids: Optional[List[str]] = Field(None, alias="mediaIds")
    primary_media_id: Optional[str] = Field(None, alias="primaryMediaId")
    gallery_media_ids: Optional[List[str]] = Field(None, alias="galleryMediaIds")
    image: Optional[str] = None
    hover_image: Optional[str] = Field(None, alias="hoverImage")
    additional_images: Optional[List[str]] = Field(None, alias="additionalImages")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("price", "original_price", "compare_at_price", mode="before")
    @classmethod
    def _coerce_int_price(cls, v):
        if v is None or v == "":
            return None
        return int(float(v))

    @field_validator("stock", "low_stock_threshold", mode="before")
    @classmethod
    def _coerce_int_stock(cls, v):
        if v is None or v == "":
            return None
        return int(float(v))

    @model_validator(mode="before")
    @classmethod
    def _reject_lifecycle_and_unsupported(cls, values):
        """
        Lifecycle state is owned by the dedicated endpoints — approve,
        reject, publish, unpublish, archive, restore, submit-review. Patching
        it here would bypass the publish gate (audit C-29), so these keys are
        rejected with an explicit message rather than silently dropped.
        """
        if isinstance(values, dict):
            blocked = [
                key
                for key in (
                    "status", "published", "review", "review_flags",
                    "reviewFlags", "history", "price_history", "priceHistory",
                    "createdBy", "created_by", "updatedBy", "updated_by",
                    "collection", "collections", "collectionIds", "collection_ids",
                )
                if key in values
            ]
            if blocked:
                collection_keys = {"collection", "collections", "collectionIds", "collection_ids"}
                if collection_keys.intersection(blocked):
                    raise ValueError(
                        "Product collection membership is managed by the collection "
                        "assignment endpoint; collection fields are read-only. Blocked: "
                        + ", ".join(sorted(set(blocked)))
                    )
                raise ValueError(
                    "Product lifecycle fields cannot be written through this "
                    "endpoint; use the lifecycle routes (submit-review, approve, "
                    "reject, publish, unpublish, archive, restore). Blocked: "
                    + ", ".join(sorted(set(blocked)))
                + ". Fields the backend does not store (e.g. variants, "
                  "department) are ignored, never persisted."
                )
        return values


class ProductCreateRequest(ProductContentFields):
    """POST /admin/products — create a product with server-allocated id."""


class ProductDraftRequest(ProductContentFields):
    """
    POST /admin/products/draft — create a DRAFT under a caller-supplied
    permanent ID.  The ID is the canonical `catalog_product.id`; the same
    value is mirrored into `product_id` (the stable UI label column).
    """

    id: str = Field(..., description="Permanent product ID, pattern ^[A-Z0-9][A-Z0-9-]{1,35}$")

    @field_validator("id")
    @classmethod
    def validate_product_id(cls, v: str) -> str:
        if not PRODUCT_ID_RE.match(v):
            raise ValueError("Product ID must match ^[A-Z0-9][A-Z0-9-]{1,35}$")
        return v


class ProductUpdateRequest(ProductContentFields):
    """
    PATCH /admin/products/{id} — field-level admin patch.

    Only explicitly set fields are applied (`exclude_unset` semantics in the
    service), so a partial save never overwrites untouched columns with stale
    values. Lifecycle/status keys are rejected (see the base validator).
    """


# 30-field whitelist from API_CONTRACT.md for employee edits
EMPLOYEE_EDITABLE_FIELDS = {
    "name", "price", "compare_at_price", "compareAtPrice",
    "description", "short_description", "shortDescription",
    "category", "subcategory", "gender", "fabric", "material",
    "primary_color", "primaryColor", "secondary_color", "secondaryColor",
    "colors", "patterns", "work", "occasion", "sizes", "season",
    "fit", "length", "highlights", "care_instructions", "careInstructions",
    "tags",
    "stock", "availability",
}


class EmployeeProductUpdateRequest(BaseModel):
    """PATCH /employee/products/{id} — whitelisted employee edit."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: Optional[str] = None
    price: Optional[int] = None
    compare_at_price: Optional[int] = Field(None, alias="compareAtPrice")
    description: Optional[str] = None
    short_description: Optional[str] = Field(None, alias="shortDescription")
    category: Optional[str] = None
    subcategory: Optional[str] = None
    gender: Optional[str] = None
    fabric: Optional[str] = None
    material: Optional[str] = None
    primary_color: Optional[str] = Field(None, alias="primaryColor")
    secondary_color: Optional[str] = Field(None, alias="secondaryColor")
    colors: Optional[List[str]] = None
    patterns: Optional[List[str]] = None
    work: Optional[List[str]] = None
    occasion: Optional[List[str]] = None
    sizes: Optional[List[str]] = None
    season: Optional[str] = None
    fit: Optional[str] = None
    length: Optional[str] = None
    highlights: Optional[List[Any]] = None
    care_instructions: Optional[List[Any]] = Field(None, alias="careInstructions")
    tags: Optional[List[str]] = None
    stock: Optional[int] = None
    availability: Optional[str] = None


class AssignEmployeeRequest(BaseModel):
    employee_id: Optional[str] = Field(None, alias="employeeId", min_length=1)

    model_config = ConfigDict(populate_by_name=True)


class RejectProductRequest(BaseModel):
    reason: str = Field(..., min_length=1)


class ChangeProductIdRequest(BaseModel):
    new_id: str = Field(..., alias="newId")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("new_id")
    @classmethod
    def validate_new_id(cls, v: str) -> str:
        if not PRODUCT_ID_RE.match(v):
            raise ValueError("Product ID must match ^[A-Z0-9][A-Z0-9-]{1,35}$")
        return v


class BulkUpdateRequest(BaseModel):
    product_ids: List[str] = Field(..., alias="productIds")
    updates: Dict[str, Any]

    model_config = ConfigDict(populate_by_name=True)


class ClearReviewFlagsRequest(BaseModel):
    """
    Clear specific review flags.

    Plan §9.2 flags this route as "no vocabulary validation — declare the flag
    vocabulary" (§24 step 8).  `flags` is now checked against
    `REVIEW_FLAG_VALUES`; an unknown flag is a 422 naming it, rather than a
    silent 200 that clears nothing.
    """

    flags: List[str]

    @field_validator("flags")
    @classmethod
    def validate_flags(cls, v: List[str]) -> List[str]:
        unknown = [f for f in v if f not in REVIEW_FLAG_VALUES]
        if unknown:
            raise ValueError(
                "Unknown review flag(s): "
                + ", ".join(sorted(set(unknown)))
                + ". Supported flags: "
                + ", ".join(REVIEW_FLAG_VALUES)
                + "."
            )
        return v


# ── Query params ──────────────────────────────────────────────────────────────

class ProductListQuery(BaseModel):
    """Query parameters for GET /products (storefront catalogue)."""

    q: Optional[str] = None
    category: Optional[Union[str, List[str]]] = None
    subcategory: Optional[Union[str, List[str]]] = None
    gender: Optional[Union[str, List[str]]] = None
    price: Optional[Union[str, List[str]]] = None
    size: Optional[Union[str, List[str]]] = None
    color: Optional[Union[str, List[str]]] = None
    fabric: Optional[Union[str, List[str]]] = None
    material: Optional[Union[str, List[str]]] = None
    occasion: Optional[Union[str, List[str]]] = None
    collection: Optional[Union[str, List[str]]] = None
    rating: Optional[Union[str, List[str]]] = None
    availability: Optional[Union[str, List[str]]] = None
    sort: str = "recommended"
    page: int = 1
    page_size: int = Field(20, alias="pageSize")
    # Internal — set by GET /collections/{id}/products to restrict results
    # to the pre-resolved membership set. Not a public query parameter.
    collection_product_ids: Optional[List[str]] = None

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("sort")
    @classmethod
    def resolve_sort(cls, v: str) -> str:
        resolved = SORT_ALIASES.get(v, v)
        if resolved not in VALID_SORTS:
            return "recommended"
        return resolved


class AdminProductListQuery(BaseModel):
    """Query parameters for GET /admin/products."""

    status: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    assigned_employee_id: Optional[str] = Field(None, alias="assignedEmployeeId")
    q: Optional[str] = None
    sort: str = "newest"
    page: int = 1
    page_size: int = Field(25, alias="pageSize", ge=1, le=500)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("sort")
    @classmethod
    def resolve_sort(cls, v: str) -> str:
        return v if v in ADMIN_SORTS else "newest"


# ── Response envelopes ────────────────────────────────────────────────────────

class ProductListResponse(BaseModel):
    ok: bool = True
    items: List[StorefrontProduct] = []
    total: int = 0
    page: int = 1
    page_size: int = Field(20, alias="pageSize")
    facets: FacetCounts = Field(default_factory=FacetCounts)
    applied_filters: Dict[str, Any] = Field({}, alias="appliedFilters")

    model_config = ConfigDict(populate_by_name=True)


class AdminProductListResponse(BaseModel):
    ok: bool = True
    items: List[AdminProduct] = []
    total: int = 0
    page: int = 1
    page_size: int = Field(25, alias="pageSize")

    model_config = ConfigDict(populate_by_name=True)


class SingleProductResponse(BaseModel):
    ok: bool = True
    product: AdminProduct


class SingleEmployeeProductResponse(BaseModel):
    ok: bool = True
    product: EmployeeProduct


class StorefrontProductResponse(BaseModel):
    ok: bool = True
    product: Optional[StorefrontProduct] = None


class PublishIssuesResponse(BaseModel):
    ok: bool = True
    issues: List[str] = []


class AvailabilityResponse(BaseModel):
    ok: bool = True
    sku_taken: bool = Field(False, alias="skuTaken")
    slug_taken: bool = Field(False, alias="slugTaken")
    suggested_slug: Optional[str] = Field(None, alias="suggestedSlug")

    model_config = ConfigDict(populate_by_name=True)


class NextIdResponse(BaseModel):
    ok: bool = True
    next_id: str = Field(..., alias="nextId")

    model_config = ConfigDict(populate_by_name=True)


class CatalogMetricsResponse(BaseModel):
    ok: bool = True
    total: int = 0
    draft: int = 0
    pending_review: int = Field(0, alias="pendingReview")
    published: int = 0
    archived: int = 0
    unassigned: int = 0
    blocked: int = 0

    model_config = ConfigDict(populate_by_name=True)


class RecommendationsResponse(BaseModel):
    ok: bool = True
    items: List[StorefrontProduct] = []


class RecentlyViewedResponse(BaseModel):
    ok: bool = True
    items: List[StorefrontProduct] = []


class OkResponse(BaseModel):
    ok: bool = True
    message: Optional[str] = None


class ErrorResponse(BaseModel):
    ok: bool = False
    error: Optional[str] = None
    errors: Optional[List[str]] = None

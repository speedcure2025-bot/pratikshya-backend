"""
Collection — Pydantic schemas.

Response shapes follow API_CONTRACT.md § COLLECTIONS verbatim.

Key points:
  - displayStatus is DERIVED from (status, startDate, endDate) — it is never
    sent by the client and never stored; the service sets it before returning.
  - resolvedProductCount is computed server-side.
  - rule shape: { flag?, occasion?, fabricIncludes? }
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────

class CollectionStatusEnum(str, Enum):
    DRAFT = "DRAFT"
    SCHEDULED = "SCHEDULED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    EXPIRED = "EXPIRED"
    ARCHIVED = "ARCHIVED"


class CollectionTypeEnum(str, Enum):
    MANUAL = "MANUAL"
    RULE_BASED = "RULE_BASED"


# ─────────────────────────────────────────────────────────────────────────────
# Collection rule schema
# ─────────────────────────────────────────────────────────────────────────────

class CollectionRule(BaseModel):
    flag: Optional[str] = None
    occasion: Optional[str] = None
    fabricIncludes: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Public response shape
# ─────────────────────────────────────────────────────────────────────────────

class CollectionResponse(BaseModel):
    """
    Public collection shape — verbatim from API_CONTRACT.md § COLLECTIONS.

    { id, name, slug, eyebrow, description, image, heroMediaId,
      thumbnailMediaId, type, status, displayStatus, featured, sortOrder,
      startDate, endDate, rule, explicitProductIds[], resolvedProductCount }
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    name: str
    slug: str
    eyebrow: Optional[str] = ""
    description: Optional[str] = ""
    image: Optional[str] = ""
    heroMediaId: Optional[str] = Field(None, alias="hero_media_id", serialization_alias="heroMediaId")
    thumbnailMediaId: Optional[str] = Field(None, alias="thumbnail_media_id", serialization_alias="thumbnailMediaId")
    type: str = "MANUAL"
    status: str
    displayStatus: str = ""          # derived, injected by service
    featured: bool = False
    sortOrder: int = Field(0, alias="sort_order", serialization_alias="sortOrder")
    startDate: Optional[datetime] = Field(None, alias="start_date", serialization_alias="startDate")
    endDate: Optional[datetime] = Field(None, alias="end_date", serialization_alias="endDate")
    rule: Optional[Dict[str, Any]] = None
    explicitProductIds: List[str] = Field(default_factory=list, alias="explicit_product_ids", serialization_alias="explicitProductIds")
    resolvedProductCount: int = 0    # injected by service


# ─────────────────────────────────────────────────────────────────────────────
# Create / Update request bodies
# ─────────────────────────────────────────────────────────────────────────────

class CollectionCreateRequest(BaseModel):
    """Body for POST /admin/collections."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str
    slug: Optional[str] = None              # auto-derived from name if omitted
    eyebrow: Optional[str] = ""
    description: Optional[str] = ""
    image: Optional[str] = Field("", alias="imageUrl")
    hero_media_id: Optional[str] = Field(None, alias="heroMediaId")
    thumbnail_media_id: Optional[str] = Field(None, alias="thumbnailMediaId")
    type: CollectionTypeEnum = CollectionTypeEnum.MANUAL
    featured: bool = False
    sort_order: int = Field(0, alias="sortOrder")
    start_date: Optional[datetime] = Field(None, alias="startDate")
    end_date: Optional[datetime] = Field(None, alias="endDate")
    # MANUAL collections
    explicit_product_ids: Optional[List[str]] = Field(None, alias="explicitProductIds")
    # RULE_BASED collections
    rule: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def validate_dates(self) -> "CollectionCreateRequest":
        if self.start_date is not None and self.end_date is not None:
            if self.end_date < self.start_date:
                raise ValueError("endDate must be greater than or equal to startDate")
        return self


class CollectionUpdateRequest(BaseModel):
    """Body for PATCH /admin/collections/{id} — all fields optional."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: Optional[str] = None
    slug: Optional[str] = None
    eyebrow: Optional[str] = None
    description: Optional[str] = None
    image: Optional[str] = Field(None, alias="imageUrl")
    hero_media_id: Optional[str] = Field(None, alias="heroMediaId")
    thumbnail_media_id: Optional[str] = Field(None, alias="thumbnailMediaId")
    type: Optional[CollectionTypeEnum] = None
    featured: Optional[bool] = None
    sort_order: Optional[int] = Field(None, alias="sortOrder")
    start_date: Optional[datetime] = Field(None, alias="startDate")
    end_date: Optional[datetime] = Field(None, alias="endDate")
    explicit_product_ids: Optional[List[str]] = Field(None, alias="explicitProductIds")
    rule: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def validate_dates(self) -> "CollectionUpdateRequest":
        if self.start_date is not None and self.end_date is not None:
            if self.end_date < self.start_date:
                raise ValueError("endDate must be greater than or equal to startDate")
        return self


class AssignProductsRequest(BaseModel):
    """
    Body for PUT /admin/collections/{id}/products.
    Replaces the full explicit product list (MANUAL collections only).
    Sources: assignProductsToCollection / addProductsToCollection /
             removeProductsFromCollection in the frontend.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    productIds: List[str] = Field(alias="product_ids")


# ─────────────────────────────────────────────────────────────────────────────
# Response wrappers — keep the { ok: true, … } envelope
# ─────────────────────────────────────────────────────────────────────────────

class CollectionListResponse(BaseModel):
    ok: bool = True
    items: List[CollectionResponse]


class SingleCollectionResponse(BaseModel):
    ok: bool = True
    collection: CollectionResponse


class TaxonomyCollectionMetrics(BaseModel):
    """Collection totals and status counts returned by taxonomy metrics."""

    model_config = ConfigDict(populate_by_name=True)

    total: int
    by_status: Dict[str, int] = Field(..., alias="byStatus")


class TaxonomyEntityCount(BaseModel):
    """Total for one taxonomy entity kind."""

    total: int


class TaxonomyMetricsResponse(BaseModel):
    """Successful wire shape for ``GET /admin/taxonomy/metrics``."""

    model_config = ConfigDict(populate_by_name=True)

    ok: bool
    collections: TaxonomyCollectionMetrics
    categories: TaxonomyEntityCount
    subcategories: TaxonomyEntityCount


class TaxonomyProductCountItem(BaseModel):
    """Resolved product count for one collection."""

    model_config = ConfigDict(populate_by_name=True)

    collection_id: str = Field(..., alias="collectionId")
    name: str
    product_count: int = Field(..., alias="productCount")


class TaxonomyProductCountsResponse(BaseModel):
    """Successful wire shape for ``GET /admin/taxonomy/product-counts``."""

    model_config = ConfigDict(populate_by_name=True)

    ok: bool
    counts: List[TaxonomyProductCountItem]


class OkResponse(BaseModel):
    ok: bool = True
    message: Optional[str] = None
